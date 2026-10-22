"""A busy delivery retries in the daemon, under a durable starter fence."""

import asyncio
import json

import pytest

from quorus.protocol import SocialVerb
from quorus.runtime import failover, handoff_delivery, handoff_runtime, handoff_sender
from quorus.runtime.handoff_state import fingerprint
from tests.test_handoff_delivery import Relay
from tests.test_unfinished_wakes import _git, reflexd

pytest_plugins = ["tests.test_handoff_delivery"]


def policy():
    return dict(detect=lambda name: "claude" if name == "arav-gemini" else "codex",
                capabilities=lambda _: [], score=lambda **_: (1, "yes"))


class Candidates(Relay):
    async def fetch_presence(self):
        return [{"name": n, "online": True, "status": "active"}
                for n in ["b-codex", "c-codex", "d-codex", "e-codex"]]

    async def fetch_members(self, **kw):
        return [e["name"] for e in await self.fetch_presence()]


def test_three_busy_declines_retry_without_hop_increment_and_survive_restart(wiring, monkeypatch):
    _, daemon, wt, cap, data, env = wiring
    relay = Candidates()
    monkeypatch.setattr(handoff_sender, "snapshot", lambda *a: cap)
    root = {"room": "build", "message_id": cap.root, "content": "finish"}
    result = asyncio.run(handoff_sender.attempt(
        daemon, relay, root, reason="quota", mission={"task_id": "task"},
        worktree=wt, bindings=reflexd.ROOM_BINDINGS_PATH, **policy()))
    assert result == "sent"
    store = handoff_runtime.journal(daemon, "build", wt)
    assert handoff_runtime.blocked(daemon, "build", wt)
    alerts = []
    monkeypatch.setattr(daemon, "_alert_owner", lambda *args: alerts.append(args))
    for count in range(3):
        target = relay.defers[-1]["target"]
        relay.holder = daemon.config.participant_name  # receiver returned the claim
        verb = SocialVerb(verb="defer", actor=target, room_id="canonical-room-uuid",
                          ref_message_id=cap.root, payload={"to": daemon.config.participant_name,
                          "decline": {"capsule_id": cap.id, "fingerprint": fingerprint(cap),
                                      "task_id": "task", "reason": "busy"}})
        decline = {"room": "build", "from_name": target, "message_type": "social",
                   "content": verb.model_dump_json()}
        asyncio.run(handoff_sender.retry(daemon, relay, decline, wt, **policy()))
        # The same response under a different envelope id cannot retry a second time.
        asyncio.run(handoff_sender.retry(daemon, relay, decline, wt, **policy()))
        assert store.read(cap)["declines"] == ["b-codex", "c-codex", "d-codex"][:count + 1]
    assert len(relay.defers) == 3
    assert all(x["failover"]["handoffs"] == 1 for x in relay.defers)
    assert alerts and handoff_runtime.blocked(daemon, "build", wt)
    # Reload from disk with a new daemon object: returning quota does not unfence.
    restarted = reflexd.Reflexd(daemon.config)
    assert handoff_runtime.blocked(restarted, "build", wt)


def test_partial_busy_transfer_can_replay_its_typed_decline(wiring, monkeypatch):
    _, daemon, wt, cap, data, env = wiring
    relay = Relay()
    original = relay.post_social_defer

    async def fail(**kw):
        raise OSError("lost response")
    monkeypatch.setattr(relay, "post_social_defer", fail)
    with pytest.raises(OSError):
        asyncio.run(handoff_delivery.busy(daemon, relay, env, failover.continuation(env), wt))
    assert relay.holder == daemon.config.participant_name  # rejected delivery retains claim
    monkeypatch.setattr(relay, "post_social_defer", original)
    asyncio.run(handoff_delivery.busy(daemon, relay, env, failover.continuation(env), wt))
    assert len(relay.defers) == 1


def test_sender_fence_precedes_claim_transfer_and_blocks_an_ordinary_turn(wiring, monkeypatch):
    repo, daemon, wt, cap, data, env = wiring
    relay = Candidates()
    monkeypatch.setattr(handoff_sender, "snapshot", lambda *a: cap)
    send = relay.post_social_defer

    async def fenced_send(**kw):
        assert handoff_runtime.blocked(daemon, "build", wt)
        await send(**kw)
    monkeypatch.setattr(relay, "post_social_defer", fenced_send)
    asyncio.run(handoff_sender.attempt(
        daemon, relay, {"room": "build", "message_id": cap.root}, reason="quota",
        mission={"task_id": "task"}, worktree=wt, **policy()))
    modes = []

    async def run(*a, mode="default", **kw):
        modes.append(mode)
        return "NO_REPLY"
    monkeypatch.setattr(daemon.adapter, "run", run)
    asyncio.run(daemon._wake_and_reply(relay, {"room": "build", "message_id": "new-ask",
                "from_name": "arav", "message_type": "chat", "content": "do another task"}))
    assert modes == ["readonly"]
    assert _git(repo, "rev-parse", "main") == cap.repo.base


def test_capsule_must_match_typed_actor_root_and_hop(wiring):
    *_, env = wiring
    raw = json.loads(env["content"])
    raw["payload"]["failover"]["capsule"]["by"] = "someone-else"
    assert failover.continuation({**env, "content": json.dumps(raw)}) is None


def test_real_vendor_failure_parks_checkpoints_and_fences_before_defer(wiring, monkeypatch):
    repo, daemon, wt, _, _, _ = wiring
    relay = Candidates()
    before = _git(repo, "rev-parse", "main")

    async def run(*a, cwd, **kw):
        (cwd / "CHANGELOG.md").write_text("partial vendor-limited turn\n")
        _git(cwd, "commit", "-qam", "partial task")
        return failover.VendorFailure("⚠ quota", "quota")
    monkeypatch.setattr(daemon.adapter, "run", run)
    asyncio.run(daemon._wake_and_reply(relay, {"room": "build", "message_id": "real-root",
                "from_name": "arav", "message_type": "chat", "content": "finish the task"}))
    assert len(relay.defers) == 1
    data = relay.defers[0]["failover"]
    assert data["capsule"]["check"]["status"] == "ran"
    assert data["capsule"]["repo"]["head"] == _git(wt.path, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", "main") == before
    # Same physical tree, different room: still fenced until the sent head integrates.
    assert handoff_runtime.blocked(daemon, "a-different-room", wt)
    assert handoff_runtime.skip_sync(daemon, "a-different-room", wt)
    _git(repo, "merge", "--ff-only", wt.branch)
    assert not handoff_runtime.blocked(daemon, "a-different-room", wt)
