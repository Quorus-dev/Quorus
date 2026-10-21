"""Inactive handoff wiring preserves ordinary unfinished-turn synchronization."""

import asyncio

import pytest

from quorus.runtime import handoff_runtime, handoff_sender
from quorus.runtime.worktree_hold import marker_path
from tests.test_unfinished_wakes import TIMED_OUT, _daemon, _git, _repo, _wake, reflexd


def test_ordinary_unfinished_turn_syncs_before_harness_when_handoffs_are_off(
        monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    daemon = _daemon(monkeypatch, tmp_path, repo)
    assert handoff_sender.enabled("build", reflexd.ROOM_BINDINGS_PATH) is False
    _wake(monkeypatch, daemon, reply=TIMED_OUT, edit="unfinished ordinary work\n")
    wt = reflexd.reflexd_wake.existing_worktree(repo, daemon.config.participant_name)
    assert reflexd.reflexd_unfinished.pending(daemon._unfinished_dir, wt.path)
    (repo / "teammate.txt").write_text("new integration work\n")
    _git(repo, "add", "teammate.txt")
    _git(repo, "commit", "-qm", "teammate advances main")
    advanced = _git(repo, "rev-parse", "main")
    assert not (wt.path / "teammate.txt").exists()
    assert not handoff_runtime.skip_sync(daemon, "build", wt)
    ran = []

    async def finish(*args, cwd, mode="default", **kwargs):
        assert mode == "autonomous"
        assert (cwd / "teammate.txt").read_text() == "new integration work\n"
        assert _git(cwd, "merge-base", "HEAD", "main") == advanced
        assert (cwd / "CHANGELOG.md").read_text() == "unfinished ordinary work\n"
        ran.append(True)
        return "✅ ordinary continuation finished"

    monkeypatch.setattr(daemon.adapter, "run", finish)
    from tests.test_unfinished_wakes import _Relay
    asyncio.run(daemon._wake_and_reply(_Relay(), {
        "room": "build", "from_name": "arav", "message_id": "ordinary-retry",
        "message_type": "chat", "content": "@arav-gemini finish the interrupted task"}))
    assert ran == [True]
    assert (repo / "teammate.txt").read_text() == "new integration work\n"
    assert (repo / "CHANGELOG.md").read_text() == "unfinished ordinary work\n"


@pytest.mark.parametrize("content", ['{"handoff":null}', '{"handoff":"owned"}', '[]', '{'])
def test_handoff_or_unreadable_holds_still_skip_sync(monkeypatch, tmp_path, content):
    repo = _repo(tmp_path)
    daemon = _daemon(monkeypatch, tmp_path, repo)
    wt = reflexd.reflexd_wake.agent_worktree(repo, daemon.config.participant_name)
    marker = marker_path(daemon._unfinished_dir, wt.path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(content)
    assert handoff_runtime.skip_sync(daemon, "build", wt)
