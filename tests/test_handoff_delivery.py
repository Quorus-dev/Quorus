"""Drive proof gating and completion through the actual daemon turn path."""

import asyncio
import json
import os
import sys
from dataclasses import asdict

import pytest

from quorus.protocol import SocialVerb
from quorus.runtime import failover, handoff_delivery, handoff_runtime, handoff_sender
from quorus.runtime.check_config import CheckConfig
from quorus.runtime.checkpoint import run_checkpoint
from quorus.runtime.handoff_capsule import build_capsule
from quorus.runtime.handoff_hold import PublicationHold
from tests.test_unfinished_wakes import _daemon, _git, _Relay, _repo, reflexd


class Relay(_Relay):
    def __init__(self, holder="arav-gemini"):
        super().__init__()
        self.holder, self.defers, self.lookups = holder, [], 0
        self.change_at = None

    async def mission_task_for(self, **kw):
        self.lookups += 1
        return {"task_id": "task", "claimed_by": (
            "other" if self.change_at == self.lookups else self.holder)}

    async def release_for_failover(self, *, actor, target, **kw):
        assert actor == self.holder
        self.holder = target

    async def post_social_defer(self, **kw):
        self.defers.append(kw)


@pytest.fixture
def wiring(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    daemon = _daemon(monkeypatch, tmp_path, repo)
    sender = reflexd.reflexd_wake.agent_worktree(repo, "arav-claude")
    (sender.path / "CHANGELOG.md").write_text("unfinished\n")
    _git(sender.path, "commit", "-qam", "starter progress")
    cfg = CheckConfig((sys.executable, "-c", "print('FAILED test_x'); raise SystemExit(1)"))
    binding = json.loads(reflexd.ROOM_BINDINGS_PATH.read_text())
    binding["build"]["check"] = cfg.record()
    reflexd.ROOM_BINDINGS_PATH.write_text(json.dumps(binding))
    head = _git(sender.path, "rev-parse", "HEAD")
    cap = build_capsule(
        root="owner-request", hop=1, actor="arav-claude", task="finish",
        repo={"main": sender.main, "base": _git(repo, "rev-parse", "main"),
              "branch": sender.branch, "head": head,
              "tree": _git(sender.path, "rev-parse", "HEAD^{tree}"),
              "unfinished": sender.branch + "-unfinished"},
        checkpoint=run_checkpoint(sender.path, head, cfg, env={"PATH": os.defpath}),
        config=cfg, spent={"turns": 1, "wall_s": 1.0, "attempts": 1},
        limit={"vendor": "claude", "reason": "quota", "kind": "guess",
               "source": "msg.no_time", "seen_at": "2026-10-21T14:00:00Z"})
    data = {"original_message_id": cap.root, "task_id": "task", "task": "finish",
            "reason": "quota", "branch": sender.branch, "last_commit": head[:12],
            "failed_agents": ["arav-claude"], "handoffs": 1, "capsule": cap.model_dump()}
    verb = SocialVerb(verb="defer", actor=cap.by, room_id="build", ref_message_id=cap.root,
                      payload={"to": "arav-gemini", "failover": data})
    env = {"message_id": "delivery", "room": "build", "from_name": cap.by,
           "message_type": "social", "content": verb.model_dump_json()}
    wt = reflexd.reflexd_wake.agent_worktree(repo, daemon.config.participant_name)
    monkeypatch.setattr(handoff_sender, "enabled", lambda room, bindings: True)

    async def trusted(*a, **kw):
        return True
    monkeypatch.setattr(daemon, "_trusted_wake", trusted)
    return repo, daemon, wt, cap, data, env


@pytest.mark.parametrize("outcome", ["reproduced", "passing", "claim_lost", "disabled"])
def test_real_delivery_receipt_precedes_harness_and_only_proof_can_publish(
        wiring, monkeypatch, outcome):
    repo, daemon, wt, cap, data, env = wiring
    relay, seen = Relay(), []
    if outcome == "passing":
        # Same locally configured command, different environment result.
        monkeypatch.setattr(handoff_delivery, "verify", lambda *a, **kw: _passing(a, kw))
    if outcome == "claim_lost":
        relay.change_at = 3  # mission lookup, verify lookup, pre-launch lookup
    if outcome == "disabled":
        monkeypatch.setattr(handoff_sender, "enabled", lambda room, bindings: False)

    async def run(*a, cwd, mode="default", context, **kw):
        seen.append(mode)
        assert relay.posted and relay.posted[0]["message_type"] == "status"
        assert "HANDOFF DATA" in context
        if outcome == "reproduced":
            assert _git(cwd, "rev-parse", "HEAD") == cap.repo.head
            assert PublicationHold(cwd, handoff_runtime.journal(daemon, "build", wt), cap).owned()
            (cwd / "CHANGELOG.md").write_text("finished\n")
            _git(cwd, "commit", "-qam", "receiver fix")
        return "✅ finished"

    monkeypatch.setattr(daemon.adapter, "run", run)
    before = _git(repo, "rev-parse", "main")
    asyncio.run(daemon._wake_and_reply(relay, env))
    assert seen == ["autonomous" if outcome == "reproduced" else "readonly"]
    if outcome == "reproduced":
        assert (repo / "CHANGELOG.md").read_text() == "finished\n"
        assert _git(repo, "rev-list", "--count", f"{before}..main") == "2"
        assert "reproduced" in relay.posted[0]["content"]
    else:
        assert _git(repo, "rev-parse", "main") == before


def _passing(args, kwargs):
    from unittest.mock import patch

    from quorus.runtime.handoff_receiver import verify
    with patch("quorus.runtime.handoff_receiver.run_checkpoint",
               return_value={"status": "passing", "exit": 0}):
        return verify(*args, **kwargs)


def test_dirty_receiver_declines_without_starting_harness(wiring, monkeypatch):
    _, daemon, wt, cap, data, env = wiring
    (wt.path / "CHANGELOG.md").write_text("receiver's unsaved work")
    relay = Relay()

    async def never(*a, **kw):
        pytest.fail("busy receiver must not launch")
    monkeypatch.setattr(daemon.adapter, "run", never)
    asyncio.run(daemon._wake_and_reply(relay, env))
    assert relay.holder == cap.by
    assert relay.defers[0]["decline"]["reason"] == "busy"
    assert (wt.path / "CHANGELOG.md").read_text() == "receiver's unsaved work"
    assert _git(wt.path, "rev-parse", "HEAD") == cap.repo.base


def test_restart_rechecks_verified_proof_and_continuing_does_not_launch_twice(wiring):
    _, daemon, wt, cap, data, env = wiring
    relay = Relay()
    store = handoff_runtime.journal(daemon, "build", wt)
    with store.locked():
        store.write(cap, "received", snapshot=cap.model_dump())
    first = asyncio.run(handoff_delivery.receive(
        daemon, relay, env, failover.continuation(env), wt, trusted=True,
        mode="autonomous", bindings=reflexd.ROOM_BINDINGS_PATH))
    assert first.writable
    assert store.read(cap)["state"] == "continuing"
    duplicate = asyncio.run(handoff_delivery.receive(
        daemon, relay, env, failover.continuation(env), wt, trusted=True,
        mode="autonomous", bindings=reflexd.ROOM_BINDINGS_PATH))
    assert duplicate.drop and not duplicate.writable


def test_sender_checkpoint_builds_same_snapshot_and_retains_vendor_provenance(wiring):
    _, daemon, wt, cap, data, env = wiring
    reading, _ = reflexd.reflexd_limits.read_limit_message("retry in 60s", 1792587600.0)
    daemon.adapter.limit = reading
    daemon.adapter.limited_until = reading.resets_at + 60
    # Use the actual current reading to avoid having it aged out by current_limit().
    daemon.adapter.current_limit = lambda: reading
    made = handoff_sender.snapshot(daemon, "build", wt,
                                   {**data, "vendor": "gemini"}, reflexd.ROOM_BINDINGS_PATH)
    assert made.check.status == "ran"
    assert made.repo.head == _git(wt.path, "rev-parse", "HEAD")
    assert made.limit.presence_reading() == asdict(reading)


def test_untrusted_delivery_never_runs_owner_check(wiring, monkeypatch):
    _, daemon, wt, _, _, env = wiring

    def never(*a, **kw):
        pytest.fail("untrusted room data cannot run the configured check")
    monkeypatch.setattr(handoff_delivery, "verify", never)
    result = asyncio.run(handoff_delivery.receive(
        daemon, Relay(), env, failover.continuation(env), wt,
        trusted=False, mode="autonomous", bindings=reflexd.ROOM_BINDINGS_PATH))
    assert not result.writable


def test_failed_receipt_post_never_launches_and_keeps_the_verified_tree_held(wiring):
    _, daemon, wt, cap, _, env = wiring

    class Offline(Relay):
        async def post_reply(self, **kw):
            raise OSError("relay unavailable")
    result = asyncio.run(handoff_delivery.receive(
        daemon, Offline(), env, failover.continuation(env), wt,
        trusted=True, mode="autonomous", bindings=reflexd.ROOM_BINDINGS_PATH))
    store = handoff_runtime.journal(daemon, "build", wt)
    assert not result.writable
    assert store.read(cap)["state"] == "verified"
    assert PublicationHold(wt.path, store, cap).owned()
    assert store.read(cap)["receipt"]
    # A redelivery after restart reruns proof at that exact snapshot.
    retry = asyncio.run(handoff_delivery.receive(
        daemon, Relay(), env, failover.continuation(env), wt,
        trusted=True, mode="autonomous", bindings=reflexd.ROOM_BINDINGS_PATH))
    assert retry.writable


def test_busy_dispatch_does_not_wait_for_the_harness_lock(wiring, monkeypatch):
    _, daemon, wt, cap, _, env = wiring
    relay = Relay()

    async def never(*a, **kw):
        pytest.fail("coordination must not wake a busy harness")
    monkeypatch.setattr(daemon.adapter, "run", never)

    async def deliver():
        async with daemon._wake_lock:
            await daemon._dispatch_event(relay, "message", env)
    asyncio.run(deliver())
    assert relay.holder == cap.by and len(relay.defers) == 1
    assert "delivery" in daemon._handled_ids
