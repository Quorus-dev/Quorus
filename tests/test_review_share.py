"""Step 3: a review request carries this wake's commits as a bundle (opt-in)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quorus import bundle_client
from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app
from quorus.runtime import review_share

RELAY = "http://relay.test"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class _Wt:
    def __init__(self, path: Path) -> None:
        self.path, self.repo, self.branch, self.main = path, path, "quorus/a-claude", "main"


@pytest.fixture
def worktree(tmp_path: Path) -> _Wt:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
                 ("config", "user.name", "a-claude"),
                 ("commit", "-q", "--allow-empty", "-m", "base"),
                 ("checkout", "-qb", "quorus/a-claude")):
        _git(repo, *args)
    return _Wt(repo)


@pytest.fixture
def room(monkeypatch) -> str:
    _reset_state()
    client = TestClient(app)

    class _InProcess:
        post = staticmethod(lambda url, **kw: client.post(url.removeprefix(RELAY), **kw))
        get = staticmethod(lambda url, **kw: client.get(url.removeprefix(RELAY), **kw))

    monkeypatch.setattr(bundle_client, "_http", _InProcess)
    auth = {"Authorization": f"Bearer {_bearer('b-codex')}"}
    rid = client.post("/rooms", json={"name": "rv", "created_by": "b-codex"},
                      headers=auth).json()["id"]
    client.post(f"/rooms/{rid}/join", json={"participant": "a-claude"}, headers=auth)
    yield rid
    _reset_state()


def _bearer(name: str) -> str:
    return create_jwt(sub=name, tenant_id="t-rv", tenant_slug="rv")


def _attach(room: str, wt: _Wt, pre: str | None, reply: str) -> str | None:
    return asyncio.run(review_share.attach_bundle(
        relay_url=RELAY, bearer=_bearer("a-claude"), room=room, worktree=wt,
        pre_head=pre, reply=reply, me="a-claude"))


def test_review_request_carries_exactly_this_wakes_commits(room, worktree, tmp_path) -> None:
    pre = review_share.head_of(worktree)
    (worktree.path / "fix.txt").write_text("fix\n")
    _git(worktree.path, "add", "fix.txt")
    _git(worktree.path, "commit", "-qm", "fix: the thing")
    line = _attach(room, worktree, pre, "Done. @b-codex please review.")
    assert line and "quorus fetch b_" in line and "--room" in line
    bundle_id = line.split("quorus fetch ")[1].split()[0]

    other = tmp_path / "laptop2"  # has the base, not the fix
    _git(tmp_path, "clone", "-q", "--single-branch", "--branch", "main",
         f"file://{worktree.path}", str(other))
    got = bundle_client.fetch_into(other, *bundle_client.download(
        RELAY, _bearer("b-codex"), room, bundle_id))
    assert _git(other, "log", "-1", "--format=%s", got.ref) == "fix: the thing"


def test_nothing_shared_without_a_mention_or_new_commits(room, worktree) -> None:
    pre = review_share.head_of(worktree)
    assert _attach(room, worktree, pre, "@b-codex please review") is None  # no new commits
    _git(worktree.path, "commit", "-q", "--allow-empty", "-m", "work")
    assert _attach(room, worktree, pre, "done, nobody mentioned") is None
    assert _attach(room, worktree, pre, "note to @a-claude (myself)") is None
    assert _attach(room, worktree, None, "@b-codex review") is None  # room not opted in


def test_share_is_opt_in_per_room(tmp_path) -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "reflexd", Path(__file__).resolve().parent.parent / "scripts" / "reflexd.py")
    reflexd = sys.modules.get("reflexd") or importlib.util.module_from_spec(spec)
    if "reflexd" not in sys.modules:
        sys.modules["reflexd"] = reflexd
        spec.loader.exec_module(reflexd)
    bindings = tmp_path / "room-bindings.json"
    bindings.write_text(json.dumps({"lan": {"path": "/x", "share": True},
                                    "dev": {"path": "/y"}, "odd": {"share": "yes"}}))
    assert reflexd.share_for("lan", bindings_path=bindings) is True
    assert reflexd.share_for("dev", bindings_path=bindings) is False
    assert reflexd.share_for("odd", bindings_path=bindings) is False  # only literal true
    assert reflexd.share_for("missing", bindings_path=tmp_path / "nope.json") is False


def test_daemon_attaches_the_bundle_to_a_review_request(room, tmp_path, monkeypatch) -> None:
    """The reflexd hook end to end: bound repo with share on, a wake that
    commits and asks @b-codex to review; the posted reply carries a fetch line."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "reflexd", Path(__file__).resolve().parent.parent / "scripts" / "reflexd.py")
    reflexd = sys.modules.get("reflexd") or importlib.util.module_from_spec(spec)
    if "reflexd" not in sys.modules:
        sys.modules["reflexd"] = reflexd
        spec.loader.exec_module(reflexd)

    repo = tmp_path / "bound"
    repo.mkdir()
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
                 ("config", "user.name", "t"), ("commit", "-q", "--allow-empty", "-m", "base")):
        _git(repo, *args)
    laptop2 = tmp_path / "laptop2"  # has the base from before the wake, not the fix
    _git(tmp_path, "clone", "-q", "--single-branch", "--branch", "main",
         f"file://{repo}", str(laptop2))
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps({"rv": {"path": str(repo), "mode": "autonomous",
                                           "trust": "members", "share": True}}))
    monkeypatch.setattr(reflexd, "ROOM_BINDINGS_PATH", bindings)
    monkeypatch.setattr(reflexd.reflexd_wake, "WORKTREES_ENABLED", True)
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url=RELAY, api_key="k",
                                              participant_name="a-claude",
                                              runtime_dir=tmp_path / "rt"))

    async def fake_run(harness, *, context, cwd=None, **_kw) -> str:
        (Path(cwd) / "fix.txt").write_text("fix\n")
        _git(Path(cwd), "add", "fix.txt")
        _git(Path(cwd), "-c", "user.email=a@a", "-c", "user.name=a-claude",
             "commit", "-qm", "fix: shared for review")
        return "Fixed it. @b-codex please review."

    monkeypatch.setattr(d.adapter, "run", fake_run)
    posted: list[str] = []

    class _Relay:
        async def fetch_recent(self, *, room: str, limit: int = 10):
            return []

        async def post_reply(self, **kw):
            posted.append(kw["content"])
            return {"id": "p"}

        async def bearer_jwt(self) -> str:
            return _bearer("a-claude")

    env = {"id": "e", "message_id": "m", "room": "rv", "from_name": "b-codex",
           "content": "@a-claude fix it", "message_type": "chat"}
    triage = reflexd.classify_message(content=env["content"], sender="b-codex",
                                      self_name="a-claude")
    asyncio.run(d._wake_and_reply(_Relay(), env, triage=triage))
    reply = "\n".join(posted)
    assert "quorus fetch b_" in reply, reply
    bundle_id = reply.split("quorus fetch ")[1].split()[0]
    got = bundle_client.fetch_into(laptop2, *bundle_client.download(
        RELAY, _bearer("b-codex"), room, bundle_id))
    assert _git(laptop2, "log", "-1", "--format=%s", got.ref) == "fix: shared for review"
