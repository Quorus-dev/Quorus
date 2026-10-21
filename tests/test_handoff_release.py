"""Never Stuck slice 2c-2: `quorus handoff holds` and `release` with their safeguards.

Dogfood plan §1/§5 (Codex GO on cfd69e70). A retained hold is made the real way:
a delivery records the capsule, the receiver's proof says `already_passing`, and
the rollback fails, so the work stays held at the capsule head (as in
test_handoff_receiver.test_failed_rollback_retains_publication_hold).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from quorus.runtime import handoff_receiver as receiver
from quorus.runtime import handoff_release as hr
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_hold import PublicationHold
from quorus.runtime.handoff_state import Journal, StateError
from tests.test_handoff_receiver import case  # noqa: F401  (fixture)

AGENT = "receiver-codex"


@pytest.fixture
def held(case, tmp_path, monkeypatch):  # noqa: F811
    c = case
    runtime = tmp_path / "runtime"
    c.journal = Journal(hr.journal_path(runtime, AGENT), "room", c.root,
                        hold_dir=runtime / "unfinished-wakes")
    original = Git.run

    def fail_restore(self, *args):
        if args[0] == "update-ref" and "quorus restore handoff" in args:
            raise StateError("simulated git failure")
        return original(self, *args)

    monkeypatch.setattr(Git, "run", fail_restore)
    out = receiver.verify(c.root, c.journal, c.capsule,
                          **{**c.args, "env": {**c.args["env"], "EXIT": "0"}})
    monkeypatch.setattr(Git, "run", original)
    assert out.outcome == "already_passing"
    # Delivery records the capsule snapshot on "received" (handoff_delivery.py:109);
    # receiver.verify alone doesn't, so add it the way the journal would hold it.
    data = json.loads(c.journal.path.read_text())
    (key,) = data
    data[key]["snapshot"] = c.capsule.model_dump()
    c.journal.path.write_text(json.dumps(data))
    assert PublicationHold(c.root, c.journal, c.capsule).owned()
    head = c.capsule.repo.head
    assert c.git("rev-parse", "HEAD") == head
    return SimpleNamespace(c=c, runtime=runtime, head=head)


def _release(h, **kw):
    args = dict(agent=AGENT, room="room", capsule_id=h.c.capsule.id, head=h.head,
                action="discard", daemon_running=False)
    return hr.release(h.runtime, **{**args, **kw})


def test_holds_lists_the_held_work(held):
    (one,) = hr.holds(held.runtime)
    assert (one.room, one.capsule_id, one.state, one.agent) == (
        "room", held.c.capsule.id, "already_passing", AGENT)
    assert one.worktree.resolve() == held.c.root.resolve()


def test_discard_goes_back_to_prev_keeps_a_backup_and_lifts_the_hold(held):
    c = held.c
    note = _release(held)
    assert c.git("rev-parse", "HEAD") == c.base  # the journal's prev
    ref = hr.backup_ref(c.capsule.id)
    assert ":" not in ref and c.git("rev-parse", ref) == held.head
    assert not PublicationHold(c.root, c.journal, c.capsule).owned()
    assert "discarded" in note
    log = [json.loads(x) for x in (held.runtime / hr.RELEASE_LOG).read_text().splitlines()]
    assert log[-1]["action"] == "discard" and log[-1]["head"] == held.head


def test_publish_lifts_the_hold_and_moves_nothing(held):
    c = held.c
    _release(held, action="publish")
    assert c.git("rev-parse", "HEAD") == held.head
    assert c.git("rev-parse", hr.backup_ref(c.capsule.id)) == held.head
    assert not PublicationHold(c.root, c.journal, c.capsule).owned()


@pytest.mark.parametrize("change, reason", [
    (dict(daemon_running=True), "stop @"),
    (dict(head="abc123"), "full 40-character"),
    (dict(room="other"), "no single hold"),
    (dict(agent="someone-else"), "no single hold"),
    (dict(capsule_id="nope"), "no single hold"),
    (dict(action="push"), "--publish or --discard"),
])
def test_refusals_change_nothing(held, change, reason):
    with pytest.raises(hr.Refused, match=reason):
        _release(held, **change)
    _assert_untouched(held)


def _assert_untouched(h):
    c = h.c
    assert c.git("rev-parse", "HEAD") == h.head
    assert PublicationHold(c.root, c.journal, c.capsule).owned()
    assert c.git("for-each-ref", hr.backup_ref(c.capsule.id)) == ""


def test_a_branch_that_moved_since_review_is_refused(held):
    c = held.c
    (c.root / "tracked").write_text("more")
    c.git("commit", "-qam", "moved after review")
    with pytest.raises(hr.Refused, match="moved since you reviewed"):
        _release(held)
    assert PublicationHold(c.root, c.journal, c.capsule).owned()


def test_a_dirty_tree_is_refused(held):
    (held.c.root / "tracked").write_text("uncommitted")
    with pytest.raises(hr.Refused, match="uncommitted"):
        _release(held)
    assert PublicationHold(held.c.root, held.c.journal, held.c.capsule).owned()


def test_a_running_gate_holds_the_journal_lock(held):
    with held.c.journal.locked():
        with pytest.raises(hr.Refused, match="lock unavailable"):
            _release(held)
    _assert_untouched(held)


def test_a_backup_ref_pointing_elsewhere_is_refused(held):
    c = held.c
    c.git("update-ref", hr.backup_ref(c.capsule.id), c.base)
    with pytest.raises(hr.Refused, match="already points elsewhere"):
        _release(held)
    assert c.git("rev-parse", "HEAD") == held.head


def test_a_marker_for_another_capsule_is_refused(held):
    c = held.c
    from quorus.runtime.worktree_hold import marker_path

    marker = marker_path(c.journal.hold_dir, c.root)
    value = json.loads(marker.read_text())
    marker.write_text(json.dumps({**value, "handoff": "0" * 64}))
    with pytest.raises(hr.Refused, match="no single hold"):
        _release(held)
    assert json.loads(marker.read_text())["handoff"] == "0" * 64


def test_the_cli_needs_a_terminal_and_the_typed_room(held, monkeypatch):
    import quorus_cli.handoff_cmd as cmd

    args = SimpleNamespace(runtime_dir=str(held.runtime), room="room", agent=AGENT,
                           capsule=held.c.capsule.id, head=held.head, publish=False,
                           discard=True)
    monkeypatch.setattr(cmd.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        cmd.cmd_release(args)
    monkeypatch.setattr(cmd.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _p: "rooom")
    with pytest.raises(SystemExit):
        cmd.cmd_release(args)
    _assert_untouched(held)
    monkeypatch.setattr("builtins.input", lambda _p: "room")
    monkeypatch.setattr(cmd, "daemon_running", lambda name: False)
    cmd.cmd_release(args)
    assert held.c.git("rev-parse", "HEAD") == held.c.base


# ── Codex on ac0bc90b ───────────────────────────────────────────────────


def test_an_unknown_daemon_status_refuses(held):
    with pytest.raises(hr.Refused, match="couldn't tell"):
        _release(held, daemon_running=None)
    _assert_untouched(held)


def test_a_failed_process_lookup_is_unknown_not_stopped(monkeypatch):
    import quorus_cli.handoff_cmd as cmd

    monkeypatch.setattr("quorus_cli.agent_cmd._running_daemon_for", lambda name: None)

    def broken(*_a, **_k):
        raise OSError("no pgrep")

    monkeypatch.setattr(cmd.subprocess, "run", broken)
    assert cmd.daemon_running("a-codex") is None


def test_a_daemon_holding_its_lock_blocks_the_release_and_vice_versa(held):
    path = hr.daemon_lock_path(held.runtime, AGENT)
    fd = hr.hold_daemon_lock(path)  # what reflexd takes at startup
    try:
        with pytest.raises(hr.Refused, match="holds its lock"):
            _release(held)
        _assert_untouched(held)
    finally:
        import os

        os.close(fd)
    original = hr._release

    def check_lock_held(*a, **kw):
        assert hr.hold_daemon_lock(path) is None  # a daemon starting now can't
        return original(*a, **kw)

    hr._release = check_lock_held
    try:
        _release(held, action="publish")
    finally:
        hr._release = original
    fd = hr.hold_daemon_lock(path)  # released afterwards
    assert fd is not None


def test_a_commit_landing_after_the_backup_is_never_lifted(held, monkeypatch):
    c = held.c
    original = Git.run

    def commit_after_backup(self, *args):
        out = original(self, *args)
        if args[:2] == ("update-ref", hr.backup_ref(c.capsule.id)):
            (c.root / "tracked").write_text("sneaky")
            c.git("commit", "-qam", "landed mid-release")
        return out

    monkeypatch.setattr(Git, "run", commit_after_backup)
    with pytest.raises(hr.Refused, match="changed during the release"):
        _release(held, action="publish")
    assert PublicationHold(c.root, c.journal, c.capsule).owned()


def test_the_decision_is_on_disk_before_the_hold_goes(held, monkeypatch):
    def disk_full(runtime_dir, record):
        raise OSError("disk full")

    monkeypatch.setattr(hr, "_log", disk_full)
    with pytest.raises(OSError):
        _release(held, action="publish")
    _assert_untouched(held)  # nothing moved, hold kept, no backup


def test_a_discard_that_crashed_before_lifting_the_hold_resumes(held, monkeypatch):
    c = held.c
    original = PublicationHold.release

    def crash(self):
        raise StateError("crashed after the branch moved")

    monkeypatch.setattr(PublicationHold, "release", crash)
    with pytest.raises(hr.Refused, match="crashed"):
        _release(held)
    assert c.git("rev-parse", "HEAD") == c.base  # moved, still held
    assert PublicationHold(c.root, c.journal, c.capsule).owned()
    monkeypatch.setattr(PublicationHold, "release", original)
    assert "discarded" in _release(held)  # the owner's rerun finishes it
    assert not PublicationHold(c.root, c.journal, c.capsule).owned()
    steps = [json.loads(x)["step"]
             for x in (held.runtime / hr.RELEASE_LOG).read_text().splitlines()]
    assert steps == ["decided", "done"]
