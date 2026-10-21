"""Only a clean proof-authorized continuation can release its publication hold."""

import asyncio
import os
import sys

import pytest

from quorus.runtime.check_config import CheckConfig
from quorus.runtime.checkpoint import run_checkpoint
from quorus.runtime.handoff_capsule import build_capsule
from quorus.runtime.handoff_completion import Continuation
from quorus.runtime.handoff_hold import PublicationHold
from quorus.runtime.handoff_receiver import complete, launch, verify
from quorus.runtime.handoff_state import Journal
from tests.test_unfinished_wakes import _daemon, _git, _Relay, _repo, reflexd


def _continuation(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    daemon = _daemon(monkeypatch, tmp_path, repo)
    sender = reflexd.reflexd_wake.agent_worktree(repo, "starter-claude")
    (sender.path / "CHANGELOG.md").write_text("starter's progress\n")
    _git(sender.path, "commit", "-qam", "starter progress")
    config = CheckConfig((sys.executable, "-c", "print('FAILED test_x'); raise SystemExit(1)"))
    head = _git(sender.path, "rev-parse", "HEAD")
    capsule = build_capsule(
        root="owner-request", hop=1, actor="starter-claude", task="finish the task",
        repo={"main": sender.main, "base": _git(repo, "rev-parse", sender.main),
              "branch": sender.branch, "head": head,
              "tree": _git(sender.path, "rev-parse", "HEAD^{tree}"),
              "unfinished": sender.branch + "-unfinished"},
        checkpoint=run_checkpoint(sender.path, head, config, env={"PATH": os.defpath}),
        config=config, spent={"turns": 1, "wall_s": 1.0, "attempts": 1},
        limit={"vendor": "claude", "reason": "quota", "kind": "guess",
               "source": "msg.no_time", "seen_at": "2026-10-21T14:00:00Z"})
    wt = reflexd.reflexd_wake.agent_worktree(repo, daemon.config.participant_name)
    journal = Journal(daemon.config.runtime_dir / "handoffs.json", "build", repo,
                      hold_dir=daemon._unfinished_dir)
    assert verify(wt.path, journal, capsule, branch=wt.branch, integration=wt.main,
                  config=config, claimed=True, held=False, env={"PATH": os.defpath}).writable
    assert launch(wt.path, journal, capsule, claimed=True)
    turn = Continuation(capsule, journal, wt.path, "build", "task")
    daemon._handoff_turns["continuation-wake"] = turn
    return repo, daemon, wt, turn


@pytest.mark.parametrize("failure", [None, "harness", "claim", "uncommitted"])
def test_real_daemon_completion_publishes_only_the_verified_clean_turn(
        monkeypatch, tmp_path, failure):
    repo, daemon, wt, turn = _continuation(monkeypatch, tmp_path)
    main_before = _git(repo, "rev-parse", "main")

    class Relay(_Relay):
        holder = daemon.config.participant_name

        async def mission_task_for(self, **kwargs):
            return {"task_id": "task", "claimed_by": self.holder}

    relay = Relay()

    async def run(*_args, cwd=None, **_kwargs):
        assert PublicationHold(wt.path, turn.journal, turn.capsule).owned()
        (cwd / "CHANGELOG.md").write_text("finished by receiver\n")
        if failure != "uncommitted":
            _git(cwd, "commit", "-qam", "finish continued task")
        if failure == "claim":
            relay.holder = "someone-else"
        return "[reflexd] harness timed out" if failure == "harness" else "✅ task finished"

    monkeypatch.setattr(daemon.adapter, "run", run)
    envelope = {"id": "delivery", "message_id": "continuation-wake", "room": "build",
                "from_name": "arav", "content": "@arav-gemini continue the task",
                "message_type": "chat"}
    triage = reflexd.classify_message(content=envelope["content"], sender="arav",
                                      self_name=daemon.config.participant_name)
    asyncio.run(daemon._wake_and_reply(relay, envelope, triage=triage))
    hold = PublicationHold(wt.path, turn.journal, turn.capsule)
    if failure is None:
        assert _git(repo, "rev-parse", "main") != main_before
        assert (repo / "CHANGELOG.md").read_text() == "finished by receiver\n"
        assert turn.journal.read(turn.capsule)["state"] == "done"
        assert not hold.path.exists()
        assert any("published 2 commit(s)" in x["content"] for x in relay.posted)
    else:
        assert _git(repo, "rev-parse", "main") == main_before
        assert turn.journal.read(turn.capsule)["state"] == "continuing"
        assert hold.owned()
        asyncio.run(daemon._publish_sweep(relay))
        assert _git(repo, "rev-parse", "main") == main_before


def test_crash_after_durable_finish_releases_only_at_exact_completed_head(monkeypatch, tmp_path):
    _, _, wt, turn = _continuation(monkeypatch, tmp_path)

    def crash(self):
        raise OSError("crash before unlink")

    with monkeypatch.context() as patch:
        patch.setattr(PublicationHold, "release", crash)
        with pytest.raises(OSError):
            complete(wt.path, turn.journal, turn.capsule, claimed=True)
    assert turn.journal.read(turn.capsule)["state"] == "done"
    assert PublicationHold(wt.path, turn.journal, turn.capsule).owned()
    assert complete(wt.path, turn.journal, turn.capsule, claimed=True)


def test_a_finished_record_cannot_release_a_later_turns_work(monkeypatch, tmp_path):
    _, _, wt, turn = _continuation(monkeypatch, tmp_path)

    def crash(self):
        raise OSError("crash before unlink")

    with monkeypatch.context() as patch:
        patch.setattr(PublicationHold, "release", crash)
        with pytest.raises(OSError):
            complete(wt.path, turn.journal, turn.capsule, claimed=True)
    _git(wt.path, "commit", "--allow-empty", "-qm", "another turn's work")
    assert not complete(wt.path, turn.journal, turn.capsule, claimed=True)
    assert PublicationHold(wt.path, turn.journal, turn.capsule).owned()
