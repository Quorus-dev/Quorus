"""Room-scoped git bundles through the relay (docs/weekend/cross-machine-code.md)."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app
from quorus.routes import bundles as bundle_routes
from quorus.services import bundle_svc
from quorus.services.bundle_svc import (
    BundleError,
    BundleService,
    QuotaError,
    parse_header,
    valid_branch,
)

_TID = "t-bundles"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "a.txt").write_text("a\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-qm", "base")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = _repo(tmp_path / "laptop2")
    _git(path, "checkout", "-qb", "fix/relay")
    (path / "b.txt").write_text("fix\n")
    _git(path, "add", "b.txt")
    _git(path, "commit", "-qm", "fix the relay")
    return path


def _bundle(repo: Path, *revs: str) -> bytes:
    out = repo / "x.bundle"
    _git(repo, "bundle", "create", str(out), *revs)
    return out.read_bytes()


def _headers(name: str) -> dict[str, str]:
    token = create_jwt(sub=name, tenant_id=_TID, tenant_slug="bundles", role="user")
    return {"Authorization": f"Bearer {token}"}


# ── header parsing ──────────────────────────────────────────────────────────


def test_parse_real_incremental_bundle(repo: Path) -> None:
    header = parse_header(_bundle(repo, "main..fix/relay"))
    assert header.branch == "fix/relay"
    assert header.head == _git(repo, "rev-parse", "fix/relay")
    assert header.prerequisites == (_git(repo, "rev-parse", "main"),)


@pytest.mark.parametrize("name,ok", [
    ("fix/relay", True), ("feature-1.2", True), ("quorus/arav-codex", True),
    ("foo.lock", False), ("foo//bar", False), ("../x", False), ("a..b", False),
    (".hidden", False), ("x/.y", False), ("trail/", False), ("dot.", False),
    ("@", False), ("a@{1}", False), ("sp ace", False), ("x" * 101, False),
])
def test_branch_names_follow_git_rules(name: str, ok: bool) -> None:
    assert valid_branch(name) is ok


@pytest.mark.parametrize("blob,reason", [
    (b"PK\x03\x04zip", "not a git bundle"),
    (b"# v2 git bundle\n" + b"0" * 70_000, "header"),
    (b"# v3 git bundle\n@filter=blob:none\n" + b"a" * 40 + b" refs/heads/x\n\n", "capability"),
    (b"# v2 git bundle\n" + b"a" * 40 + b" refs/tags/v1\n\n", "refs/heads"),
    (b"# v2 git bundle\n" + b"a" * 40 + b" refs/heads/x.lock\n\n", "refs/heads"),
    (b"# v2 git bundle\nnot-an-oid refs/heads/x\n\n", "malformed"),
])
def test_parse_rejects_unsafe_headers(blob: bytes, reason: str) -> None:
    with pytest.raises(BundleError, match=reason):
        parse_header(blob)


def test_parse_rejects_two_branches(repo: Path) -> None:
    with pytest.raises(BundleError, match="exactly one branch"):
        parse_header(_bundle(repo, "main", "fix/relay"))


# ── store: quotas, expiry, isolation, disk ──────────────────────────────────


async def test_participant_quota_is_enforced(repo: Path, monkeypatch) -> None:
    data = _bundle(repo, "main..fix/relay")
    monkeypatch.setattr(bundle_svc, "PARTICIPANT_QUOTA_BYTES", len(data) * 2)
    svc = BundleService()
    await svc.put(_TID, "r1", "bob", data)
    await svc.put(_TID, "r1", "bob", data)
    with pytest.raises(QuotaError):
        await svc.put(_TID, "r1", "bob", data)
    await svc.put(_TID, "r1", "carol", data)  # quotas are per participant


async def test_expired_bundle_is_gone_on_read_not_only_on_sweep(repo: Path) -> None:
    now = [1000.0]
    svc = BundleService(clock=lambda: now[0])
    meta = await svc.put(_TID, "r1", "bob", _bundle(repo, "main..fix/relay"))
    assert await svc.get(_TID, "r1", meta.id) is not None
    now[0] += bundle_svc.BUNDLE_TTL_S + 1
    assert await svc.get(_TID, "r1", meta.id) is None
    assert await svc.list(_TID, "r1") == []


async def test_bundle_is_only_readable_from_its_room_and_tenant(repo: Path) -> None:
    svc = BundleService()
    meta = await svc.put(_TID, "r1", "bob", _bundle(repo, "main..fix/relay"))
    assert await svc.get(_TID, "r2", meta.id) is None
    assert await svc.get("other-tenant", "r1", meta.id) is None


async def test_total_relay_quota_is_enforced(repo: Path, monkeypatch) -> None:
    data = _bundle(repo, "main..fix/relay")
    monkeypatch.setattr(bundle_svc, "TOTAL_QUOTA_BYTES", len(data))
    svc = BundleService()
    await svc.put(_TID, "r1", "bob", data)
    with pytest.raises(QuotaError, match="relay"):
        await svc.put("another-tenant", "r9", "dan", data)


async def test_download_racing_an_expiry_sweep_never_errors(repo: Path) -> None:
    now = [1000.0]
    svc = BundleService(clock=lambda: now[0])
    meta = await svc.put(_TID, "r1", "bob", _bundle(repo, "main..fix/relay"))
    now[0] += bundle_svc.BUNDLE_TTL_S + 1
    results = await asyncio.gather(svc.list(_TID, "r1"), svc.get(_TID, "r1", meta.id),
                                   svc.list(_TID, "r1"), svc.get(_TID, "r1", meta.id))
    assert results == [[], None, [], None]


def test_multi_instance_relay_gets_no_bundle_service() -> None:
    from quorus.relay import _bundle_service

    assert _bundle_service(redis_conn=object()) is None
    assert isinstance(_bundle_service(redis_conn=None), BundleService)


def test_concurrent_upload_slots_are_bounded() -> None:
    svc = BundleService()
    assert svc.begin_upload(_TID, "bob") and svc.begin_upload(_TID, "bob")
    assert not svc.begin_upload(_TID, "bob")
    svc.end_upload(_TID, "bob")
    assert svc.begin_upload(_TID, "bob")


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def room(client: AsyncClient) -> str:
    resp = await client.post("/rooms", json={"name": "share", "created_by": "alice"},
                             headers=_headers("alice"))
    rid = resp.json()["id"]
    await client.post(f"/rooms/{rid}/join", json={"participant": "bob", "role": "member"},
                      headers=_headers("alice"))
    return rid


async def test_round_trip_lets_a_teammate_git_show_the_commit(
        client: AsyncClient, room: str, repo: Path, tmp_path: Path) -> None:
    data = _bundle(repo, "main..fix/relay")
    up = await client.post(f"/v1/rooms/{room}/bundles", content=data, headers=_headers("bob"))
    assert up.status_code == 200, up.text
    meta = up.json()
    assert meta["uploader"] == "bob" and meta["branch"] == "fix/relay"

    listed = await client.get(f"/v1/rooms/{room}/bundles", headers=_headers("alice"))
    assert [b["id"] for b in listed.json()["bundles"]] == [meta["id"]]
    down = await client.get(f"/v1/rooms/{room}/bundles/{meta['id']}", headers=_headers("alice"))
    assert down.content == data
    assert down.headers["x-quorus-bundle-sha256"] == meta["sha256"]

    # Laptop 1 has only the base; the reviewed commit must arrive by bundle.
    laptop1 = tmp_path / "laptop1"
    _git(tmp_path, "clone", "-q", "--single-branch", "--branch", "main",
         f"file://{repo}", str(laptop1))
    missing = subprocess.run(["git", "cat-file", "-e", meta["head"]], cwd=laptop1)
    assert missing.returncode != 0, "laptop 1 must not have the head before fetching"
    (tmp_path / "in.bundle").write_bytes(down.content)
    _git(laptop1, "fetch", "-q", str(tmp_path / "in.bundle"),
         "refs/heads/fix/relay:refs/quorus/bob/fix/relay")
    assert "fix the relay" in _git(laptop1, "show", "--stat", meta["head"])
    assert _git(laptop1, "branch", "--list", "fix/relay") == ""

    history = await client.get(f"/rooms/{room}/history", headers=_headers("alice"))
    notes = [m for m in history.json() if m.get("message_type") == "bundle"]
    assert notes and meta["id"] in notes[-1]["content"]


async def test_shared_secret_cannot_share_or_fetch(client: AsyncClient, room: str,
                                                   repo: Path) -> None:
    legacy = {"Authorization": "Bearer test-secret"}
    up = await client.post(f"/v1/rooms/{room}/bundles",
                           content=_bundle(repo, "main..fix/relay"), headers=legacy)
    assert up.status_code == 401
    assert (await client.get(f"/v1/rooms/{room}/bundles", headers=legacy)).status_code == 401


async def test_non_member_is_refused(client: AsyncClient, room: str, repo: Path) -> None:
    up = await client.post(f"/v1/rooms/{room}/bundles",
                           content=_bundle(repo, "main..fix/relay"), headers=_headers("mallory"))
    assert up.status_code == 403


async def test_oversize_and_invalid_bundles_are_refused(
        client: AsyncClient, room: str, monkeypatch) -> None:
    monkeypatch.setattr(bundle_routes, "MAX_BUNDLE_BYTES", 100)
    big = await client.post(f"/v1/rooms/{room}/bundles", content=b"x" * 101,
                            headers=_headers("bob"))
    assert big.status_code == 413
    bad = await client.post(f"/v1/rooms/{room}/bundles", content=b"not a bundle",
                            headers=_headers("bob"))
    assert bad.status_code == 400


async def test_upload_rate_limit(client: AsyncClient, room: str, repo: Path,
                                 monkeypatch) -> None:
    monkeypatch.setattr(bundle_routes, "UPLOADS_PER_HOUR", 1)
    data = _bundle(repo, "main..fix/relay")
    url = f"/v1/rooms/{room}/bundles"
    assert (await client.post(url, content=data, headers=_headers("bob"))).status_code == 200
    assert (await client.post(url, content=data, headers=_headers("bob"))).status_code == 429


async def test_multi_instance_relay_without_shared_disk_says_so(
        client: AsyncClient, room: str) -> None:
    app.state.bundle_service = None
    resp = await client.get(f"/v1/rooms/{room}/bundles", headers=_headers("bob"))
    assert resp.status_code == 501
