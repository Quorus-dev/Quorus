"""Room-scoped git bundles carried by the relay (docs/design/cross-machine-code.md).

The relay is a courier, not a code host: it validates the bundle header,
enforces size, quota and TTL, and hands the bytes back to room members.
Bundles live in this process's memory only. A relay running several
instances can't share them, so it gets no BundleService and the routes
answer 501 (review of ffe25d3b: a shared directory without shared metadata
let one instance delete another's live bundles).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field

MAX_BUNDLE_BYTES = int(os.environ.get("QUORUS_MAX_BUNDLE_BYTES", str(10 * 1024 * 1024)))
BUNDLE_TTL_S = int(os.environ.get("QUORUS_BUNDLE_TTL_S", str(24 * 3600)))
PARTICIPANT_QUOTA_BYTES = int(os.environ.get("QUORUS_BUNDLE_QUOTA_BYTES", str(50 * 1024 * 1024)))
TENANT_QUOTA_BYTES = int(os.environ.get("QUORUS_BUNDLE_TENANT_QUOTA_BYTES",
                                        str(200 * 1024 * 1024)))
TOTAL_QUOTA_BYTES = int(os.environ.get("QUORUS_BUNDLE_TOTAL_BYTES", str(512 * 1024 * 1024)))
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
    """Bundles and their metadata, in memory, behind one lock."""

    def __init__(self, *, clock=time.time) -> None:
        self._clock = clock
        self._meta: dict[str, BundleMeta] = {}
        self._blobs: dict[str, bytes] = {}
        self._lock = asyncio.Lock()
        self._uploading: dict[tuple[str, str], int] = {}

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
            self._meta.pop(bid, None)
            self._blobs.pop(bid, None)

    async def put(self, tenant_id: str, room_id: str, uploader: str, data: bytes) -> BundleMeta:
        if len(data) > MAX_BUNDLE_BYTES:
            raise BundleError(f"bundle exceeds {MAX_BUNDLE_BYTES} bytes")
        header = parse_header(data)
        async with self._lock:
            self._sweep()
            sizes = list(self._meta.values())
            mine = sum(m.size for m in sizes
                       if m.tenant_id == tenant_id and m.uploader == uploader)
            tenant = sum(m.size for m in sizes if m.tenant_id == tenant_id)
            if mine + len(data) > PARTICIPANT_QUOTA_BYTES:
                raise QuotaError("your shared bundles exceed your quota; wait for them to expire")
            if tenant + len(data) > TENANT_QUOTA_BYTES:
                raise QuotaError("this workspace's shared bundles exceed its quota")
            if sum(m.size for m in sizes) + len(data) > TOTAL_QUOTA_BYTES:
                raise QuotaError("this relay is holding too many bundles; try again later")
            now = self._clock()
            meta = BundleMeta(
                id="b_" + secrets.token_urlsafe(9), room_id=room_id, uploader=uploader,
                branch=header.branch, head=header.head,
                prerequisites=list(header.prerequisites), size=len(data),
                sha256=hashlib.sha256(data).hexdigest(), created_at=now,
                expires_at=now + BUNDLE_TTL_S, tenant_id=tenant_id,
            )
            self._meta[meta.id] = meta
            self._blobs[meta.id] = data
            return meta

    async def get(self, tenant_id: str, room_id: str, bid: str) -> tuple[BundleMeta, bytes] | None:
        """The bundle, or None when unknown, expired, or from another room.

        Metadata and bytes are read under the same lock that expiry deletes
        under, so a sweep can never remove the bytes mid-download."""
        async with self._lock:
            meta = self._meta.get(bid)
            if meta is None or meta.tenant_id != tenant_id or meta.room_id != room_id:
                return None
            if not self._live(meta):
                self._sweep()
                return None
            return meta, self._blobs[bid]

    async def list(self, tenant_id: str, room_id: str) -> list[BundleMeta]:
        async with self._lock:
            self._sweep()
            return sorted((m for m in self._meta.values()
                           if m.tenant_id == tenant_id and m.room_id == room_id),
                          key=lambda m: m.created_at)


class RedisBundleService(BundleService):
    """Bundles shared by every relay instance through Redis.

    Each bundle is one hash (metadata + base64 bytes) with a TTL, so a read
    gets both atomically and Redis drops both at expiry. A sorted set per room
    indexes live ids. Quota check and insert are one WATCH/MULTI transaction,
    so two instances can't both squeeze past a quota. Concurrent-upload slots stay
    per instance."""

    _SIZES = "qb:sizes"  # member "tenant|uploader|id|size", score = expires_at
    _RETRIES = 20

    def __init__(self, redis, *, clock=time.time) -> None:
        super().__init__(clock=clock)
        self._r = redis

    async def _check_quota(self, live: list[str], tenant_id: str, uploader: str,
                           size: int) -> None:
        mine = tenant = total = 0
        for member in live:
            tid, who, _bid, used = member.rsplit("|", 3)
            total += int(used)
            if tid == tenant_id:
                tenant += int(used)
                mine += int(used) if who == uploader else 0
        if mine + size > PARTICIPANT_QUOTA_BYTES:
            raise QuotaError("your shared bundles exceed your quota; wait for them to expire")
        if tenant + size > TENANT_QUOTA_BYTES:
            raise QuotaError("this workspace's shared bundles exceed its quota")
        if total + size > TOTAL_QUOTA_BYTES:
            raise QuotaError("this relay is holding too many bundles; try again later")

    async def put(self, tenant_id: str, room_id: str, uploader: str, data: bytes) -> BundleMeta:
        """Quota check and insert as one optimistic transaction: WATCH the size
        index, read live usage, then MULTI/EXEC. Any other writer touching the
        index between our read and EXEC aborts it, and we retry; no lease that
        can expire mid-write (review of e165de8b)."""
        import base64
        import json

        from redis.exceptions import WatchError

        if len(data) > MAX_BUNDLE_BYTES:
            raise BundleError(f"bundle exceeds {MAX_BUNDLE_BYTES} bytes")
        header = parse_header(data)
        encoded = base64.b64encode(data).decode("ascii")
        for _ in range(self._RETRIES):
            async with self._r.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(self._SIZES)
                    now = self._clock()
                    live = await pipe.zrangebyscore(self._SIZES, f"({now}", "+inf")
                    await self._check_quota(live, tenant_id, uploader, len(data))
                    meta = BundleMeta(
                        id="b_" + secrets.token_urlsafe(9), room_id=room_id, uploader=uploader,
                        branch=header.branch, head=header.head,
                        prerequisites=list(header.prerequisites), size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), created_at=now,
                        expires_at=now + BUNDLE_TTL_S, tenant_id=tenant_id,
                    )
                    key = f"qb:{tenant_id}:b:{meta.id}"
                    index = f"qb:{tenant_id}:room:{room_id}"
                    pipe.multi()
                    pipe.zremrangebyscore(self._SIZES, "-inf", now)
                    pipe.hset(key, mapping={"meta": json.dumps(asdict(meta)), "data": encoded})
                    pipe.expire(key, BUNDLE_TTL_S)
                    pipe.zadd(index, {meta.id: meta.expires_at})
                    pipe.expire(index, BUNDLE_TTL_S)
                    pipe.zadd(self._SIZES, {f"{tenant_id}|{uploader}|{meta.id}|{len(data)}":
                                            meta.expires_at})
                    await pipe.execute()
                    return meta
                except WatchError:
                    await asyncio.sleep(0.01)
        raise QuotaError("the relay is busy storing other bundles; try again")

    async def _fields(self, tenant_id: str, bid: str, *fields: str) -> list:
        return await self._r.hmget(f"qb:{tenant_id}:b:{bid}", *fields)

    async def get(self, tenant_id: str, room_id: str, bid: str) -> tuple[BundleMeta, bytes] | None:
        import base64
        import json

        raw_meta, raw_data = await self._fields(tenant_id, bid, "meta", "data")  # one atomic read
        if raw_meta is None or raw_data is None:
            return None
        meta = BundleMeta(**json.loads(raw_meta))
        if meta.room_id != room_id or meta.tenant_id != tenant_id or not self._live(meta):
            return None
        return meta, base64.b64decode(raw_data)

    async def list(self, tenant_id: str, room_id: str) -> list[BundleMeta]:
        import json

        index = f"qb:{tenant_id}:room:{room_id}"
        await self._r.zremrangebyscore(index, "-inf", self._clock())
        out = []
        for bid in await self._r.zrange(index, 0, -1):
            raw = (await self._fields(tenant_id, bid, "meta"))[0]
            if raw is not None:
                meta = BundleMeta(**json.loads(raw))
                if self._live(meta):
                    out.append(meta)
        return sorted(out, key=lambda m: m.created_at)
