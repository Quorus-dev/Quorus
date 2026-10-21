"""Immutable adoption, interrupted-state recovery and proof-before-write gates."""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from quorus.runtime import handoff_ownership as ownership
from quorus.runtime import handoff_receiver as receiver
from quorus.runtime.check_config import CheckConfig
from quorus.runtime.checkpoint import run_checkpoint
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_capsule import build_capsule
from quorus.runtime.handoff_state import Duplicate, Journal, StateError

SOURCE = ("import os,sys,time; from pathlib import Path; "
          "print(os.getenv('OUTPUT','FAILED test_x')); "
          "Path('tracked').write_text('changed') if os.getenv('EDIT') else None; "
          "time.sleep(3) if os.getenv('SLEEP') else None; "
          "sys.exit(int(os.getenv('EXIT','1')))")


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root).decode().strip()

    git("init", "-qb", "main")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    (root / "tracked").write_text("base")
    git("add", "tracked")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    git("branch", "quorus/receiver-codex")
    git("checkout", "-qb", "quorus/sender-claude")
    (root / "tracked").write_text("unfinished")
    git("commit", "-qam", "sender progress")
    head = git("rev-parse", "HEAD")
    config = CheckConfig((sys.executable, "-c", SOURCE), timeout_s=0.3)
    env = {"PATH": os.defpath}
    checkpoint = run_checkpoint(root, head, config, env=env)
    assert checkpoint["status"] == "ran"
    capsule = build_capsule(
        root="owner-request", hop=1, actor="sender-claude", task="finish the fix",
        repo={"main": "main", "base": base, "branch": "quorus/sender-claude",
              "head": head, "tree": git("rev-parse", "HEAD^{tree}"),
              "unfinished": "quorus/sender-claude-unfinished"},
        checkpoint=checkpoint, config=config,
        limit={"vendor": "claude", "reason": "quota", "kind": "guess",
               "source": "msg.no_time", "seen_at": "2026-10-21T14:00:00Z"},
        spent={"turns": 1, "wall_s": 10.0, "attempts": 1})
    git("checkout", "-q", "quorus/receiver-codex")
    journal = Journal(tmp_path / "runtime" / "handoffs.json", "room", root)
    args = dict(branch="quorus/receiver-codex", integration="main", config=config,
                claimed=True, held=False, env=env)
    return SimpleNamespace(root=root, git=git, base=base, capsule=capsule,
                           config=config, journal=journal, args=args)


def verify(c, **kwargs):
    return receiver.verify(c.root, c.journal, c.capsule, **{**c.args, **kwargs})


def test_reproduces_adopts_checked_out_branch_and_launches_once(case):
    c = case
    proof = verify(c)
    assert proof.outcome == "reproduced" and proof.writable
    assert c.git("rev-parse", "HEAD") == c.capsule.repo.head
    assert (c.root / "tracked").read_text() == "unfinished"
    assert c.git("rev-list", "--count", f"{c.base}..HEAD") == "1"
    assert c.journal.read(c.capsule)["state"] == "verified"
    assert receiver.launch(c.root, c.journal, c.capsule, claimed=True)
    assert not receiver.launch(c.root, c.journal, c.capsule, claimed=True)
    with pytest.raises(Duplicate):
        verify(c, recovering=True)
    with c.journal.locked():
        c.journal.finish(c.capsule)
    assert c.journal.read(c.capsule)["state"] == "done"


@pytest.mark.parametrize("env,outcome", [
    ({"EXIT": "0"}, "already_passing"), ({"EXIT": "2"}, "stale"),
    ({"OUTPUT": "different ordering"}, "weak"),
    ({"EDIT": "1"}, "unverifiable"), ({"SLEEP": "1"}, "unverifiable"),
])
def test_real_receiver_outcomes_enforce_mode(case, env, outcome):
    c = case
    proof = verify(c, env={**c.args["env"], **env})
    assert proof.outcome == outcome
    assert proof.writable == (outcome == "weak")
    assert receiver.launch(c.root, c.journal, c.capsule, claimed=True) == proof.writable
    if "EDIT" in env:
        assert (c.root / "tracked").read_text() == "changed"  # owner recovery, not discarded


@pytest.mark.parametrize("how", ["dirty", "untracked", "ahead", "held"])
def test_busy_receiver_declines_and_preserves_work(case, how):
    c = case
    if how == "dirty":
        (c.root / "tracked").write_text("my work")
    if how == "untracked":
        (c.root / "mine").write_text("my work")
    if how == "ahead":
        c.git("commit", "--allow-empty", "-qm", "receiver progress")
    before = c.git("rev-parse", "HEAD")
    assert verify(c, held=how == "held").outcome == "declined"
    assert c.git("rev-parse", "HEAD") == before
    if how == "dirty":
        assert (c.root / "tracked").read_text() == "my work"
    if how == "untracked":
        assert (c.root / "mine").read_text() == "my work"
    assert not receiver.launch(c.root, c.journal, c.capsule, claimed=True)


@pytest.mark.parametrize("operation", ["update-ref", "read-tree"])
def test_restart_recovers_before_and_after_ref_move(case, monkeypatch, operation):
    c = case
    original = Git.run

    def crash(self, *args):
        if args[0] == operation:
            raise RuntimeError("simulated daemon crash")
        return original(self, *args)

    with monkeypatch.context() as patch:
        patch.setattr(Git, "run", crash)
        with pytest.raises(RuntimeError, match="simulated"):
            verify(c)
    assert c.journal.read(c.capsule)["state"] == "adopting"
    assert verify(c, recovering=True).outcome == "reproduced"
    assert c.git("rev-parse", "HEAD") == c.capsule.repo.head


def test_restart_does_not_overwrite_edits_after_ref_move(case, monkeypatch):
    c = case
    original = Git.run

    def crash(self, *args):
        if args[0] == "read-tree":
            raise RuntimeError("crash")
        return original(self, *args)

    with monkeypatch.context() as patch:
        patch.setattr(Git, "run", crash)
        with pytest.raises(RuntimeError):
            verify(c)
    (c.root / "tracked").write_text("new human work")
    assert verify(c, recovering=True).outcome == "unverifiable"
    assert (c.root / "tracked").read_text() == "new human work"


def test_compare_and_swap_refuses_a_branch_that_moved(case, monkeypatch):
    c = case
    original = Git.run
    moved = []

    def race(self, *args):
        if args[0] == "update-ref":
            c.git("commit", "--allow-empty", "-qm", "concurrent work")
            moved.append(c.git("rev-parse", "HEAD"))
        return original(self, *args)

    monkeypatch.setattr(Git, "run", race)
    assert verify(c).outcome == "unverifiable"
    assert c.git("rev-parse", "HEAD") == moved[0]
    assert (c.root / "tracked").read_text() == "base"


@pytest.mark.parametrize("claimed,config", [(False, True), (True, False)])
def test_missing_claim_or_config_cannot_adopt(case, claimed, config):
    c = case
    assert verify(c, claimed=claimed, config=c.config if config else None).outcome == "unverifiable"
    assert c.git("rev-parse", "HEAD") == c.base


def test_capsule_command_is_only_a_claim_and_is_never_executed(case):
    c = case
    # config_id copied from the real check does not authorize a different argv.
    check = c.capsule.check.model_copy(update={"argv": [sys.executable, "-c",
                                                 "open('owned','w').write('bad')"]})
    c.capsule = c.capsule.model_copy(update={"check": check})
    assert verify(c).outcome == "unverifiable"
    assert not (c.root / "owned").exists()
    assert c.git("rev-parse", "HEAD") == c.base


@pytest.mark.parametrize("change", ["claim", "tree"])
def test_claim_or_tree_change_after_proof_cannot_launch(case, change):
    c = case
    assert verify(c).writable
    if change == "tree":
        (c.root / "tracked").write_text("later edits")
    assert not receiver.launch(c.root, c.journal, c.capsule, claimed=change != "claim")
    assert c.journal.read(c.capsule)["state"] == "unverifiable"


def test_duplicate_dropped_but_restart_before_turn_repeats_proof(case):
    c = case
    assert verify(c).writable
    with pytest.raises(Duplicate):
        verify(c)
    assert verify(c, recovering=True).writable


def test_same_id_with_changed_data_is_rejected(case):
    c = case
    assert verify(c).writable
    c.capsule = c.capsule.model_copy(update={"task": "a different task"})
    with pytest.raises(StateError, match="reused"):
        verify(c, recovering=True)


def test_corrupt_journal_is_not_treated_as_no_prior_handoff(case):
    c = case
    c.journal.path.parent.mkdir()
    c.journal.path.write_text("broken")
    with pytest.raises(StateError):
        verify(c)
    assert c.git("rev-parse", "HEAD") == c.base


def test_passing_sender_cannot_reproduce_even_if_receiver_passes():
    assert not receiver.classify(SimpleNamespace(status="passing", exit=0),
                                 {"status": "passing", "exit": 0}).writable


def test_journal_is_private_and_scoped_to_repo_and_room(case):
    c = case
    verify(c)
    assert c.journal.path.stat().st_mode & 0o777 == 0o600
    other = Journal(c.journal.path, "other-room", c.root)
    assert other.read(c.capsule) is None
    assert json.loads(c.journal.path.read_text())


def test_sender_fence_survives_restart_until_exact_head_is_integrated(case):
    c = case
    ownership.sent(c.journal, c.capsule, "receiver-codex")
    restarted = Journal(c.journal.path, "room", c.root)
    assert ownership.fenced(restarted, Git(c.root), "main")
    # A different tip on main is insufficient; it must contain the sent commit.
    c.git("update-ref", "refs/heads/main", c.capsule.repo.head, c.base)
    assert not ownership.fenced(restarted, Git(c.root), "main")
    assert restarted.read(c.capsule)["state"] == "done"


def test_sender_fence_fails_closed_when_integration_cannot_be_read(case):
    c = case
    ownership.sent(c.journal, c.capsule, "receiver-codex")
    assert ownership.fenced(c.journal, Git(c.root), "missing-integration")
    assert c.journal.read(c.capsule)["state"] == "sent"


@pytest.mark.parametrize("task,allowed", [
    ({"task_id": "t", "claimed_by": "receiver-codex"}, True),
    ({"task_id": "t", "claimed_by": "other"}, False),
    ({"task_id": "other", "claimed_by": "receiver-codex"}, False),
    ({"task_id": "t"}, False), (None, False),
])
def test_relay_claim_must_name_the_receiver_and_exact_task(task, allowed):
    class Relay:
        async def mission_task_for(self, **kwargs):
            return task
    assert asyncio.run(ownership.owns_claim(
        Relay(), room="room", participant="receiver-codex", task_id="t")) == allowed


def test_unknown_relay_claim_never_grants_writes():
    class Relay:
        async def mission_task_for(self, **kwargs):
            raise OSError("offline")
    assert not asyncio.run(ownership.owns_claim(
        Relay(), room="room", participant="receiver-codex", task_id="t"))


def test_launch_on_a_different_branch_at_the_same_commit_is_refused(case):
    c = case
    assert verify(c).writable
    c.git("checkout", "-qb", "somebody-elses-branch")
    assert not receiver.launch(c.root, c.journal, c.capsule, claimed=True)


@pytest.mark.parametrize("state", ["received", "adopted", "verified"])
def test_restart_rechecks_proof_before_continuing(case, monkeypatch, state):
    c = case
    if state == "received":
        with c.journal.locked():
            c.journal.write(c.capsule, state)
    else:
        assert verify(c).writable
        with c.journal.locked():
            c.journal.write(c.capsule, state)
    original = receiver.run_checkpoint
    calls = []

    def checkpoint(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(receiver, "run_checkpoint", checkpoint)
    assert verify(c, recovering=True).writable
    assert calls == [True]


def test_journal_lock_refuses_another_process_gate(case):
    c = case
    with c.journal.locked():
        with pytest.raises(BlockingIOError):
            verify(c)
    assert c.git("rev-parse", "HEAD") == c.base


def test_detached_receiver_does_not_adopt(case):
    c = case
    c.git("checkout", "-q", "--detach")
    assert verify(c).outcome == "unverifiable"
    assert c.git("rev-parse", "HEAD") == c.base
