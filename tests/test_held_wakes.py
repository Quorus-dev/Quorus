"""A turn redirected mid-turn by its owner or lead isn't published.

2026-10-18: "please stand down on slice 1" reached an agent mid-turn; its
turn ended and the daemon published slice 1 anyway, so it was built twice.
The daemon now reads the room before publishing and holds the commits on a
``-held-`` ref when the owner or lead @-mentioned the agent in the meantime.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)
held = reflexd.reflexd_held
ME = "arav-gemini"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp: Path) -> Path:
    repo = tmp / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "notes.md").write_text("start\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Relay:
    def __init__(self, history: list[dict] | None = None, *, broken: bool = False) -> None:
        self.history = history or []
        self.posted: list[dict] = []
        self.broken = broken

    async def fetch_recent(self, *, room: str, limit: int = 10):
        return list(self.history)[-limit:]

    async def fetch_history(self, *, room: str, limit: int):
        if self.broken:
            raise RuntimeError("relay down")
        return list(self.history)[-limit:]

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": f"p{len(self.posted)}"}

    def say(self, sender: str, content: str) -> None:
        self.history.append({"id": f"h{len(self.history)}", "from_name": sender,
                             "content": content, "timestamp": _now()})


def _daemon(monkeypatch, tmp: Path, repo: Path, *, lead: str | None = None):
    bindings = tmp / "bindings.json"
    bindings.write_text(json.dumps({"build": {"path": str(repo), "mode": "autonomous"}}))
    monkeypatch.setattr(reflexd, "ROOM_BINDINGS_PATH", bindings)
    leads = tmp / "leads.json"
    leads.write_text(json.dumps({"build": lead} if lead else {}))
    monkeypatch.setattr(reflexd, "ROOM_LEADS_PATH", leads)
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    monkeypatch.setattr(reflexd.reflexd_wake, "WORKTREES_ENABLED", True)
    return reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                                 participant_name=ME,
                                                 runtime_dir=tmp / "runtime"))


def _wake(monkeypatch, d, relay: _Relay, *, reply: str, edit: str | None = None,
          during=None, msg: str = "m", prompts: list[str] | None = None) -> _Relay:
    async def fake_run(harness, *, context, cwd=None, mode="default", **_kw: Any) -> str:
        if prompts is not None:
            prompts.append(context)
        if edit is not None:
            (Path(cwd) / "notes.md").write_text(edit)
            _git(Path(cwd), "commit", "-qam", "docs: notes")
        if during is not None:
            during(relay)
        return reply

    monkeypatch.setattr(d.adapter, "run", fake_run)
    env = {"id": f"e-{msg}", "message_id": msg, "room": "build", "from_name": "arav",
           "content": f"@{ME} write the notes", "message_type": "chat"}
    triage = reflexd.classify_message(content=env["content"], sender="arav", self_name=ME)
    asyncio.run(d._wake_and_reply(relay, env, triage=triage))
    return relay


def _main_log(repo: Path) -> str:
    return _git(repo, "log", "--format=%s", "main")


def _held_refs(repo: Path) -> list[str]:
    return [b for b in _git(repo, "branch", "--format=%(refname:short)").splitlines()
            if "-held-" in b]


def _posts(relay: _Relay) -> list[str]:
    return [p["content"] for p in relay.posted]


def test_an_owner_message_mid_turn_holds_the_commits(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    relay = _wake(monkeypatch, d, _Relay(), reply="✅ notes done", edit="mine\n",
                  during=lambda r: r.say("arav", f"@{ME} please stand down on the notes"))
    assert _main_log(repo) == "init"  # nothing published
    refs = _held_refs(repo)
    assert len(refs) == 1 and _git(repo, "show", f"{refs[0]}:notes.md") == "mine"
    wt = reflexd.reflexd_wake.existing_worktree(repo, ME)
    assert _git(wt.path, "rev-parse", "HEAD") == _git(repo, "rev-parse", "main")
    assert any("held 1 commit" in c and "@arav wrote to me mid-turn" in c and refs[0] in c
               for c in _posts(relay)), _posts(relay)


def test_without_a_mid_turn_message_it_publishes_as_before(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    _wake(monkeypatch, d, _Relay(), reply="✅ notes done", edit="mine\n")
    assert _main_log(repo).splitlines()[0] == "docs: notes"
    assert _held_refs(repo) == []


@pytest.mark.parametrize("sender, content", [
    ("stranger", f"@{ME} stop"),             # not the owner or the lead
    ("arav-codex", f"@{ME} stop"),           # a teammate that doesn't lead the room
    ("arav", "nice work everyone"),          # the owner, but not to this agent
    ("arav", f"@{ME}x unrelated name"),      # a longer name isn't a mention
])
def test_other_mid_turn_messages_dont_hold(monkeypatch, tmp_path, sender, content):
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    _wake(monkeypatch, d, _Relay(), reply="✅", edit="mine\n",
          during=lambda r: r.say(sender, content))
    assert _main_log(repo).splitlines()[0] == "docs: notes"


def test_the_rooms_lead_can_redirect_too(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo, lead="arav-claude")
    _wake(monkeypatch, d, _Relay(), reply="✅", edit="mine\n",
          during=lambda r: r.say("arav-claude", f"@{ME} go on 1 and 2 instead"))
    assert _main_log(repo) == "init" and len(_held_refs(repo)) == 1


def test_an_unreadable_history_holds(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    relay = _wake(monkeypatch, d, _Relay(broken=True), reply="✅", edit="mine\n")
    assert _main_log(repo) == "init" and len(_held_refs(repo)) == 1
    assert any("couldn't be read" in c for c in _posts(relay))


def test_a_truncated_history_holds():
    since = time.time()
    later = datetime.fromtimestamp(since + 5, timezone.utc).isoformat()
    full = [{"from_name": "x", "content": "hi", "timestamp": later}] * held.HISTORY_LIMIT
    trigger, why = held.find_stop(full, me=ME, authorities={"arav"}, since=since)
    assert trigger is None and "couldn't be read in full" in why


@pytest.mark.parametrize("reply, published, warned", [
    (f"done\n{held.REAPPLY}", True, False),
    (f"done\n{held.DROP}", False, False),
    ("done, carried on with the new task", False, True),
])
def test_the_next_wake_reapplies_or_drops_and_silence_is_flagged(
        monkeypatch, tmp_path, reply, published, warned):
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    relay = _Relay()
    _wake(monkeypatch, d, relay, reply="✅", edit="mine\n",
          during=lambda r: r.say("arav", f"@{ME} hold on, review first"))
    ref = _held_refs(repo)[0]
    prompts: list[str] = []
    relay.posted.clear()
    _wake(monkeypatch, d, relay, reply=reply, msg="m2", prompts=prompts)
    assert "HELD COMMITS" in prompts[0] and ref in prompts[0] and "review first" in prompts[0]
    assert ("docs: notes" in _main_log(repo)) is published
    assert any("neither reapplied nor dropped" in c for c in _posts(relay)) is warned
    assert ref in _held_refs(repo)  # the ref stays for humans either way
    assert not held.pending(d._held_dir, reflexd.reflexd_wake.existing_worktree(repo, ME))


def test_the_window_starts_at_the_oldest_unpublished_turn(monkeypatch, tmp_path):
    """Claude's case: a later publish (here the restart sweep; a conflict wake
    behaves the same) saw no new messages, but the turn that made the commits
    was redirected: it must still be held."""
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    wt = reflexd.reflexd_wake.agent_worktree(repo, ME)
    held.mark_window(d._held_dir, wt, started_at=time.time() - 60)
    (wt.path / "notes.md").write_text("unpublished\n")
    _git(wt.path, "commit", "-qam", "docs: unpublished")
    relay = _Relay()
    relay.history.append({"id": "s", "from_name": "arav", "content": f"@{ME} stand down",
                          "timestamp": datetime.fromtimestamp(time.time() - 30,
                                                              timezone.utc).isoformat()})
    asyncio.run(d._publish_sweep(relay))
    assert _main_log(repo) == "init" and len(_held_refs(repo)) == 1


def test_a_hold_survives_a_restart(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    relay = _Relay()
    _wake(monkeypatch, d, relay, reply="✅", edit="mine\n",
          during=lambda r: r.say("arav", f"@{ME} wait"))
    restarted = _daemon(monkeypatch, tmp_path, repo)
    asyncio.run(restarted._publish_sweep(relay))  # nothing to publish: it's held
    assert _main_log(repo) == "init"
    prompts: list[str] = []
    _wake(monkeypatch, restarted, relay, reply=held.REAPPLY, msg="m2", prompts=prompts)
    assert "HELD COMMITS" in prompts[0]
    assert "docs: notes" in _main_log(repo)


def test_each_hold_gets_its_own_ref_and_a_merge_in_progress_blocks(tmp_path):
    repo = _repo(tmp_path)
    wt = reflexd.reflexd_wake.agent_worktree(repo, ME)
    runtime = tmp_path / "held"
    refs = []
    for text in ("one\n", "two\n"):
        (wt.path / "notes.md").write_text(text)
        _git(wt.path, "commit", "-qam", f"docs: {text.strip()}")
        refs.append(held.hold(runtime, wt, ME, trigger=None, why="test"))
    assert len(set(refs)) == 2 and all(refs)

    # A conflicting merge in the worktree: the commits can't move, so they
    # stay put and publishing stays blocked until the merge is gone.
    (repo / "notes.md").write_text("main side\n")
    _git(repo, "commit", "-qam", "main side")
    (wt.path / "notes.md").write_text("agent side\n")
    _git(wt.path, "commit", "-qam", "agent side")
    subprocess.run(["git", "merge", "main"], cwd=wt.path, capture_output=True)
    held.hold(runtime, wt, ME, trigger=None, why="test")
    assert held.blocks_publish(runtime, wt)
    _git(wt.path, "merge", "--abort")
    assert not held.blocks_publish(runtime, wt)


def test_a_relay_that_cant_fetch_history_holds(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    wt = reflexd.reflexd_wake.agent_worktree(repo, ME)
    (wt.path / "notes.md").write_text("x\n")
    _git(wt.path, "commit", "-qam", "docs: x")

    class Bare:
        async def post_reply(self, **kw):
            return {}

    status = asyncio.run(d._guard_publish(Bare(), "build", wt))
    assert status and "couldn't be read" in status and _main_log(repo) == "init"
