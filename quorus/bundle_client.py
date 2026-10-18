"""Client side of room-scoped git bundles: make, upload, download, fetch.

Shared by `quorus share-branch` / `quorus fetch` and the MCP tools. Fetched code only
ever lands under ``refs/quorus/<uploader>/<branch>``: never a local branch,
a checkout or a merge (docs/design/cross-machine-code.md).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from quorus.services import bundle_svc
from quorus.services.bundle_svc import BundleError, parse_header, valid_branch

_http = httpx  # swapped for an in-process client in tests

_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
RESHARE = ("bundles last 24 hours and vanish when the relay restarts; "
           "ask @{uploader} to run `quorus share-branch` again")


class BundleClientError(RuntimeError):
    """Something the user can act on; the message says what to do."""


@dataclass
class Fetched:
    uploader: str
    branch: str
    head: str
    ref: str


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise BundleClientError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc


def _default_base(repo: Path) -> str:
    for ref in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0:
            return ref
    raise BundleClientError("no main or master branch to diff against; pass --base <ref>")


def make_bundle(repo: Path, *, branch: str | None = None, base: str | None = None,
                full: bool = False) -> tuple[bytes, str]:
    """Bundle the commits on *branch* that *base* lacks (or all of them with *full*)."""
    if _git(repo, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        raise BundleClientError("this is a shallow clone, so teammates couldn't verify the "
                                "bundle; run `git fetch --unshallow` first")
    if branch is None:
        head = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if head.returncode != 0:
            raise BundleClientError("HEAD is detached; pass --branch <name>")
        branch = head.stdout.strip()
    if not valid_branch(branch):
        raise BundleClientError(f"can't share {branch!r}: not a plain branch name")
    if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}",
            check=False).returncode != 0:
        raise BundleClientError(f"no local branch {branch!r}")
    revs = [branch]
    if not full:
        base_ref = base or _default_base(repo)
        merge_base = _git(repo, "merge-base", base_ref, branch).stdout.strip()
        if _git(repo, "rev-list", "--count", f"{merge_base}..{branch}").stdout.strip() == "0":
            raise BundleClientError(f"nothing to share: {branch} has no commits beyond {base_ref}")
        revs = [f"{merge_base}..{branch}"]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "share.bundle"
        _git(repo, "bundle", "create", "--quiet", str(out), *revs)
        data = out.read_bytes()
    cap = bundle_svc.MAX_BUNDLE_BYTES
    if len(data) > cap:
        mb = len(data) / (1 << 20)
        hint = "" if full else "; it holds only your new commits already"
        raise BundleClientError(f"the bundle is {mb:.1f} MB and the relay takes at most "
                                f"{cap >> 20} MB{hint}")
    return data, branch


KEYS_FILE = "participant_keys.json"


def _keys_path(store_dir: Path | None) -> Path:
    if store_dir is None:
        from quorus.config import resolve_config_dir

        store_dir = resolve_config_dir()
    return Path(store_dir) / KEYS_FILE


def load_participant_key(relay_url: str, name: str, store_dir: Path | None = None) -> str:
    """The saved ``quorus_pk_`` key for *name* on *relay_url*, or ""."""
    path = _keys_path(store_dir)
    try:
        keys = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        keys = {}
    return keys.get(f"{relay_url.rstrip('/')}|{name}", "") if isinstance(keys, dict) else ""


def save_participant_key(relay_url: str, name: str, key: str,
                         store_dir: Path | None = None) -> None:
    from quorus.private_files import write_private_json

    path = _keys_path(store_dir)
    try:
        keys = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        keys = {}
    keys[f"{relay_url.rstrip('/')}|{name}"] = key
    write_private_json(path, keys)


def bearer(relay_url: str, *, api_key: str = "", secret: str = "", name: str = "",
           store_dir: Path | None = None) -> str:
    """A participant JWT for this relay.

    With an API key, exchange it. On a shared-secret relay, claim *name* once
    (the relay returns a ``quorus_pk_`` key, saved at 0600 in the config dir)
    and exchange that, so sharing works without any extra setup step."""
    if api_key:
        return _token(relay_url, api_key)
    if not (secret and name):
        raise BundleClientError("no credentials for this relay; run `quorus init` first")
    key = load_participant_key(relay_url, name, store_dir)
    if not key:
        resp = _http.post(f"{relay_url.rstrip('/')}/v1/auth/claim-name", json={"name": name},
                          headers={"Authorization": f"Bearer {secret}"}, timeout=15,
                          follow_redirects=False)
        if resp.status_code == 409:
            raise BundleClientError(
                f"the name {name!r} is already claimed on this relay from another machine "
                "or config; the relay owner can free it with `quorus release-name`")
        _check(resp, "claiming your name")
        key = resp.json()["participant_key"]
        save_participant_key(relay_url, name, key, store_dir)
    return _token(relay_url, key)


def _token(relay_url: str, key: str) -> str:
    # Never follow a redirect with a credential in the body (codex, dc24ee6c review).
    resp = _http.post(f"{relay_url.rstrip('/')}/v1/auth/token", json={"api_key": key},
                      timeout=15, follow_redirects=False)
    _check(resp, "signing in")
    return resp.json()["token"]


def _check(resp: httpx.Response, what: str) -> None:
    if resp.status_code < 300:
        return
    if resp.status_code < 400:
        raise BundleClientError(f"{what} failed: the relay redirected ({resp.status_code}); "
                                "check the relay URL")
    try:
        detail = resp.json().get("detail", "")
    except ValueError:
        detail = ""
    raise BundleClientError(f"{what} failed ({resp.status_code}): {detail or resp.reason_phrase}")


def upload(relay_url: str, bearer: str, room: str, data: bytes) -> dict:
    resp = _http.post(f"{relay_url.rstrip('/')}/v1/rooms/{room}/bundles", content=data,
                      headers={"Authorization": f"Bearer {bearer}"}, timeout=60)
    _check(resp, "share")
    return resp.json()


def list_bundles(relay_url: str, bearer: str, room: str) -> list[dict]:
    resp = _http.get(f"{relay_url.rstrip('/')}/v1/rooms/{room}/bundles",
                     headers={"Authorization": f"Bearer {bearer}"}, timeout=15)
    _check(resp, "list")
    return resp.json().get("bundles", [])


def download(relay_url: str, bearer: str, room: str, bundle_id: str) -> tuple[bytes, dict]:
    if not re.fullmatch(r"b_[A-Za-z0-9_-]{1,40}", bundle_id):
        raise BundleClientError(f"{bundle_id!r} is not a bundle id (they look like b_...)")
    resp = _http.get(f"{relay_url.rstrip('/')}/v1/rooms/{room}/bundles/{bundle_id}",
                     headers={"Authorization": f"Bearer {bearer}"}, timeout=60)
    if resp.status_code == 404:
        raise BundleClientError("bundle not found: " + RESHARE.format(uploader="the uploader"))
    _check(resp, "fetch")
    meta = {k.lower().removeprefix("x-quorus-bundle-"): v for k, v in resp.headers.items()
            if k.lower().startswith("x-quorus-bundle-")}
    return resp.content, meta


def fetch_into(repo: Path, data: bytes, meta: dict) -> Fetched:
    """Verify *data* and fetch its one branch into refs/quorus/<uploader>/<branch>."""
    uploader, sha = meta.get("uploader", ""), meta.get("sha256", "")
    if not _NAME.match(uploader):
        raise BundleClientError("the relay sent no valid uploader name; not fetching")
    if hashlib.sha256(data).hexdigest() != sha:
        raise BundleClientError("bundle checksum mismatch; not fetching")
    try:
        header = parse_header(data)
    except BundleError as exc:
        raise BundleClientError(f"refusing the bundle: {exc}") from exc
    if header.branch != meta.get("branch") or header.head != meta.get("head"):
        raise BundleClientError("the bundle's branch or head doesn't match the relay's record")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "in.bundle"
        path.write_bytes(data)
        verify = _git(repo, "bundle", "verify", str(path), check=False)
        if verify.returncode != 0:
            if "prerequisite" in verify.stderr:
                missing = re.findall(r"\b[0-9a-f]{40,64}\b", verify.stderr)
                short = ", ".join(m[:7] for m in missing[:3]) or "some base commits"
                raise BundleClientError(
                    f"this repo lacks {short}, which the bundle builds on; ask @{uploader} "
                    "to run `quorus share-branch --full`, or fetch their base branch first")
            raise BundleClientError(f"git rejected the bundle: {verify.stderr.strip()[:300]}")
        ref = f"refs/quorus/{uploader}/{header.branch}"
        _git(repo, "fetch", "--quiet", "--no-tags", str(path),
             f"+refs/heads/{header.branch}:{ref}")
    return Fetched(uploader=uploader, branch=header.branch, head=header.head, ref=ref)
