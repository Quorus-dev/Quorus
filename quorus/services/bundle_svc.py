"""Room-scoped git bundles carried by the relay (docs/weekend/cross-machine-code.md).

The relay is a courier, not a code host: it validates the bundle header,
enforces size, quota and TTL, and hands the bytes back to room members.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_BUNDLE_BYTES = int(os.environ.get("QUORUS_MAX_BUNDLE_BYTES", str(10 * 1024 * 1024)))
BUNDLE_TTL_S = int(os.environ.get("QUORUS_BUNDLE_TTL_S", str(24 * 3600)))
PARTICIPANT_QUOTA_BYTES = int(os.environ.get("QUORUS_BUNDLE_QUOTA_BYTES", str(50 * 1024 * 1024)))
TENANT_QUOTA_BYTES = int(os.environ.get("QUORUS_BUNDLE_TENANT_QUOTA_BYTES",
                                        str(500 * 1024 * 1024)))
MAX_CONCURRENT_UPLOADS = 2  # per participant
MAX_HEADER_BYTES = 64 * 1024

_SIGNATURES = {b"# v2 git bundle\n": 2, b"# v3 git bundle\n": 3}
_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_BRANCH_CHARS = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


class BundleError(ValueError):
    """A bundle the relay refuses; the message is safe to show the client."""


class QuotaError(BundleError):
    """Storing this bundle would exceed a participant or tenant quota."""


def valid_branch(name: str) -> bool:
    """Branch names git itself accepts (git check-ref-format), on a safe charset."""
    if not _BRANCH_CHARS.match(name) or name == "@" or "@{" in name:
        return False
    if ".." in name or "//" in name or name.startswith("/") or name.endswith(("/", ".")):
        return False
    return all(part and not part.startswith(".") and not part.endswith(".lock")
               for part in name.split("/"))


@dataclass(frozen=True)
class BundleHeader:
    version: int
    prerequisites: tuple[str, ...]
    branch: str
    head: str


def parse_header(data: bytes) -> BundleHeader:
    """Strictly parse a git bundle header: exactly one ``refs/heads/<branch>``."""
    version = next((v for sig, v in _SIGNATURES.items() if data.startswith(sig)), None)
    if version is None:
        raise BundleError("not a git bundle (v2 or v3)")
    end = data.find(b"\n\n", 0, MAX_HEADER_BYTES)
    if end < 0:
        raise BundleError("bundle header missing or too long")
    try:
        lines = data[:end].decode("ascii").split("\n")[1:]
    except UnicodeDecodeError as exc:
        raise BundleError("bundle header is not ASCII") from exc
    prereqs: list[str] = []
    refs: list[tuple[str, str]] = []
    for line in lines:
        if line.startswith("@"):
            if version != 3 or line not in ("@object-format=sha1", "@object-format=sha256"):
                raise BundleError("unsupported bundle capability")
        elif line.startswith("-"):
            oid = line[1:].split(" ", 1)[0]
            if not _OID.match(oid):
                raise BundleError("malformed prerequisite")
            prereqs.append(oid)
        else:
            oid, _, ref = line.partition(" ")
            if not _OID.match(oid) or not ref:
                raise BundleError("malformed ref line")
            refs.append((oid, ref))
    if len(refs) != 1:
        raise BundleError("a bundle must carry exactly one branch")
    head, ref = refs[0]
    branch = ref.removeprefix("refs/heads/")
    if branch == ref or not valid_branch(branch):
        raise BundleError("the bundle's ref must be a valid refs/heads/<branch>")
    return BundleHeader(version, tuple(prereqs), branch, head)


@dataclass
class BundleMeta:
    id: str
    room_id: str
    uploader: str
    branch: str
    head: str
    prerequisites: list[str]
    size: int
    sha256: str
    created_at: float
    expires_at: float
    tenant_id: str = field(repr=False, default="")

    def public(self) -> dict:
        out = asdict(self)
        out.pop("tenant_id")
        return out


class BundleService:
    """In-memory metadata; bytes in memory or, with *root*, on disk (0600)."""

    def __init__(self, root: Path | None = None, *, clock=time.time) -> None:
        self._root = root
        self._clock = clock
        self._meta: dict[str, BundleMeta] = {}
        self._blobs: dict[str, bytes] = {}
        self._lock = asyncio.Lock()
        self._uploading: dict[tuple[str, str], int] = {}
        if root is not None:
            # Metadata lives in memory, so bytes left by a previous run can
            # never be served again: drop them rather than leak disk.
            for stale in root.glob("*/*"):
                stale.unlink(missing_ok=True)

    def begin_upload(self, tenant_id: str, uploader: str) -> bool:
        """Reserve one of the uploader's concurrent upload slots."""
        key = (tenant_id, uploader)
        if self._uploading.get(key, 0) >= MAX_CONCURRENT_UPLOADS:
            return False
        self._uploading[key] = self._uploading.get(key, 0) + 1
        return True

    def end_upload(self, tenant_id: str, uploader: str) -> None:
        key = (tenant_id, uploader)
        self._uploading[key] = max(0, self._uploading.get(key, 0) - 1)

    def _live(self, meta: BundleMeta) -> bool:
        return meta.expires_at > self._clock()

    def _sweep(self) -> None:
        for bid in [b for b, m in self._meta.items() if not self._live(m)]:
            self._drop(bid)

    def _drop(self, bid: str) -> None:
        meta = self._meta.pop(bid, None)
        self._blobs.pop(bid, None)
        if meta is not None and self._root is not None:
            (self._root / meta.tenant_id / bid).unlink(missing_ok=True)

    async def put(self, tenant_id: str, room_id: str, uploader: str, data: bytes) -> BundleMeta:
        if len(data) > MAX_BUNDLE_BYTES:
            raise BundleError(f"bundle exceeds {MAX_BUNDLE_BYTES} bytes")
        header = parse_header(data)
        async with self._lock:
            self._sweep()
            mine = sum(m.size for m in self._meta.values()
                       if m.tenant_id == tenant_id and m.uploader == uploader)
            tenant = sum(m.size for m in self._meta.values() if m.tenant_id == tenant_id)
            if mine + len(data) > PARTICIPANT_QUOTA_BYTES:
                raise QuotaError("your shared bundles exceed your quota; wait for them to expire")
            if tenant + len(data) > TENANT_QUOTA_BYTES:
                raise QuotaError("this workspace's shared bundles exceed its quota")
            now = self._clock()
            meta = BundleMeta(
                id="b_" + secrets.token_urlsafe(9), room_id=room_id, uploader=uploader,
                branch=header.branch, head=header.head,
                prerequisites=list(header.prerequisites), size=len(data),
                sha256=hashlib.sha256(data).hexdigest(), created_at=now,
                expires_at=now + BUNDLE_TTL_S, tenant_id=tenant_id,
            )
            if self._root is not None:
                await asyncio.to_thread(self._write, tenant_id, meta.id, data)
            else:
                self._blobs[meta.id] = data
            self._meta[meta.id] = meta
            return meta

    def _write(self, tenant_id: str, bid: str, data: bytes) -> None:
        folder = self._root / tenant_id
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = folder / f".{bid}.tmp"
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, folder / bid)

    async def get(self, tenant_id: str, room_id: str, bid: str) -> tuple[BundleMeta, bytes] | None:
        """The bundle, or None when unknown, expired, or from another room."""
        async with self._lock:
            meta = self._meta.get(bid)
            if meta is None or meta.tenant_id != tenant_id or meta.room_id != room_id:
                return None
            if not self._live(meta):
                self._drop(bid)
                return None
        if self._root is not None:
            data = await asyncio.to_thread((self._root / tenant_id / bid).read_bytes)
        else:
            data = self._blobs[bid]
        return meta, data

    async def list(self, tenant_id: str, room_id: str) -> list[BundleMeta]:
        async with self._lock:
            self._sweep()
            return sorted((m for m in self._meta.values()
                           if m.tenant_id == tenant_id and m.room_id == room_id),
                          key=lambda m: m.created_at)
