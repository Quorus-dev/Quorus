"""`quorus share-branch` / `quorus fetch` client: real repos on two "laptops"."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quorus import bundle_client
from quorus.auth.tokens import create_jwt
from quorus.bundle_client import BundleClientError, download, fetch_into, make_bundle, upload
from quorus.relay import _reset_state, app
from quorus.services import bundle_svc

RELAY = "http://relay.test"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", f"add {name}")


@pytest.fixture
def laptop2(tmp_path: Path) -> Path:
    repo = tmp_path / "laptop2"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "a.txt", "base\n")
    _git(repo, "checkout", "-qb", "fix/relay")
    _commit(repo, "b.txt", "fix\n")
    return repo


@pytest.fixture
def laptop1(tmp_path: Path, laptop2: Path) -> Path:
    """A teammate who has main but none of the branch's commits."""
    repo = tmp_path / "laptop1"
    _git(tmp_path, "clone", "-q", "--single-branch", "--branch", "main",
         f"file://{laptop2}", str(repo))
    return repo


def _bearer(name: str) -> str:
    return create_jwt(sub=name, tenant_id="t-share", tenant_slug="share", role="user")


@pytest.fixture
def room(monkeypatch) -> str:
    """The real relay app, reached in-process by the client's HTTP calls."""
    _reset_state()
    client = TestClient(app)

    class _InProcess:
        @staticmethod
        def post(url, **kw):
            return client.post(url.removeprefix(RELAY), **kw)

        @staticmethod
        def get(url, **kw):
            return client.get(url.removeprefix(RELAY), **kw)

    monkeypatch.setattr(bundle_client, "_http", _InProcess)
    auth = {"Authorization": f"Bearer {_bearer('alice')}"}
    rid = client.post("/rooms", json={"name": "share", "created_by": "alice"},
                      headers=auth).json()["id"]
    client.post(f"/rooms/{rid}/join", json={"participant": "bob", "role": "member"},
                headers=auth)
    yield rid
    _reset_state()


def test_teammate_can_git_show_a_branch_shared_from_another_machine(
        room: str, laptop1: Path, laptop2: Path) -> None:
    head = _git(laptop2, "rev-parse", "fix/relay")
    assert subprocess.run(["git", "cat-file", "-e", head], cwd=laptop1).returncode != 0

    data, branch = make_bundle(laptop2)
    assert branch == "fix/relay"
    meta = upload(RELAY, _bearer("bob"), room, data)
    got = fetch_into(laptop1, *download(RELAY, _bearer("alice"), room, meta["id"]))

    assert got.ref == "refs/quorus/bob/fix/relay" and got.head == head
    assert "add b.txt" in _git(laptop1, "show", "--stat", head)
    assert _git(laptop1, "branch", "--list", "fix/relay") == ""  # nothing checked out
    assert _git(laptop1, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_missing_base_asks_for_a_full_share_which_then_works(
        room: str, laptop2: Path, tmp_path: Path) -> None:
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    _git(stranger, "init", "-q", "-b", "main")
    meta = upload(RELAY, _bearer("bob"), room, make_bundle(laptop2)[0])
    with pytest.raises(BundleClientError, match="share-branch --full"):
        fetch_into(stranger, *download(RELAY, _bearer("alice"), room, meta["id"]))

    full = upload(RELAY, _bearer("bob"), room, make_bundle(laptop2, full=True)[0])
    got = fetch_into(stranger, *download(RELAY, _bearer("alice"), room, full["id"]))
    assert _git(stranger, "rev-parse", got.ref) == _git(laptop2, "rev-parse", "fix/relay")


def test_expired_or_unknown_bundle_says_to_reshare(room: str) -> None:
    with pytest.raises(BundleClientError, match="share-branch` again"):
        download(RELAY, _bearer("alice"), room, "b_doesnotexist")


def test_tampered_bytes_and_bad_uploader_are_refused(
        room: str, laptop1: Path, laptop2: Path) -> None:
    meta = upload(RELAY, _bearer("bob"), room, make_bundle(laptop2)[0])
    data, headers = download(RELAY, _bearer("alice"), room, meta["id"])
    with pytest.raises(BundleClientError, match="checksum"):
        fetch_into(laptop1, data + b"x", headers)
    with pytest.raises(BundleClientError, match="uploader"):
        fetch_into(laptop1, data, {**headers, "uploader": "../../heads/main"})
    assert subprocess.run(["git", "cat-file", "-e", meta["head"]], cwd=laptop1).returncode != 0


def test_nothing_to_share_detached_and_shallow_are_explained(
        laptop2: Path, tmp_path: Path) -> None:
    _git(laptop2, "checkout", "-q", "main")
    with pytest.raises(BundleClientError, match="nothing to share"):
        make_bundle(laptop2)
    _git(laptop2, "checkout", "-q", "--detach", "fix/relay")
    with pytest.raises(BundleClientError, match="detached"):
        make_bundle(laptop2)
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{laptop2}", str(shallow))
    with pytest.raises(BundleClientError, match="shallow"):
        make_bundle(shallow, branch="main")


def test_oversize_bundle_is_refused_before_upload(laptop2: Path, monkeypatch) -> None:
    monkeypatch.setattr(bundle_svc, "MAX_BUNDLE_BYTES", 10)
    with pytest.raises(BundleClientError, match="at most"):
        make_bundle(laptop2)


async def test_mcp_tools_share_and_fetch_like_the_cli(
        room: str, laptop1: Path, laptop2: Path, monkeypatch) -> None:
    from quorus_mcp import bundle_tools, server

    monkeypatch.setattr(server, "RELAY_URL", RELAY)
    monkeypatch.setattr(server, "API_KEY", "quorus_sk_test")
    who = ["bob"]

    async def _token() -> str:
        return _bearer(who[0])

    monkeypatch.setattr(server, "_get_bearer_token", _token)
    shared = await bundle_tools.share_branch_impl(room, str(laptop2))
    assert shared.startswith("Shared fix/relay"), shared
    bundle_id = shared.split(" as ")[1].split(" ")[0]

    who[0] = "alice"
    fetched = await bundle_tools.fetch_bundle_impl(room, bundle_id, str(laptop1))
    assert "refs/quorus/bob/fix/relay" in fetched, fetched
    assert _git(laptop1, "rev-parse", "refs/quorus/bob/fix/relay") == \
        _git(laptop2, "rev-parse", "fix/relay")

    names = {t.name for t in await server.mcp.list_tools()}
    assert {"share_branch", "fetch_bundle"} <= names


async def test_mcp_tools_work_on_a_shared_secret_relay(
        room: str, laptop2: Path, tmp_path: Path, monkeypatch) -> None:
    """No API key: the MCP claims its own name once and shares as that name."""
    from quorus_mcp import bundle_tools, server

    from quorus.auth.participant_keys import ParticipantKeyStore

    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    legacy = {"Authorization": "Bearer test-secret"}
    relay = TestClient(app)
    shared_room = relay.post("/rooms", json={"name": "lan", "created_by": "alice"},
                             headers=legacy).json()["id"]
    relay.post(f"/rooms/{shared_room}/join", json={"participant": "bob"}, headers=legacy)
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(server, "RELAY_URL", RELAY)
    monkeypatch.setattr(server, "API_KEY", "")
    monkeypatch.setattr(server, "RELAY_SECRET", "test-secret")
    monkeypatch.setattr(server, "INSTANCE_NAME", "bob")
    out = await bundle_tools.share_branch_impl(shared_room, str(laptop2))
    assert out.startswith("Shared fix/relay"), out
