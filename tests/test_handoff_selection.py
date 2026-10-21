"""Rehearsal run 1: the only cross-vendor receiver was still reviewing."""

import asyncio
import json

import pytest

from quorus.protocol import SocialVerb
from quorus.runtime import failover, handoff_delivery, handoff_runtime, handoff_sender, handoff_wait
from tests.test_handoff_delivery import Relay
from tests.test_handoff_retry import policy
from tests.test_unfinished_wakes import reflexd

pytest_plugins = ["tests.test_handoff_delivery"]


def test_rehearsal_codex_has_a_positive_bid_but_busy_is_not_active():
    from scripts.reflexd_triage import capabilities_for, compute_bid_v2

    name = "arav-handoff-codex"
    options = dict(actor="arav-handoff-claude", failed=["arav-handoff-claude"],
                   task="now fix task 2 in TASKS.md",
                   detect=lambda n: "codex" if n.endswith("-codex") else "claude",
                   capabilities=capabilities_for, score=compute_bid_v2)
    entry = dict(name=name, online=True, status="busy")
    assert capabilities_for(name)
    assert failover.choose_candidate([entry], [name], **options) is None
    assert failover.choose_candidate([entry], [name], statuses=("busy",), **options) == name
    assert failover.choose_candidate([{**entry, "status": "active"}], [name], **options) == name


class Receiver(Relay):
    status = "busy"
    online = True
    member = True

    async def fetch_presence(self):
        return [dict(name="b-codex", online=self.online, status=self.status)]

    async def fetch_members(self, **kw):
        return ["b-codex"] if self.member else []


def attempt(wiring, monkeypatch, relay):
    _, daemon, wt, cap, _, _ = wiring
    snapshots = []
    original = handoff_sender.snapshot

    def snapshot(*a):
        snapshots.append(1)
        return original(*a)

    monkeypatch.setattr(handoff_sender, "snapshot", snapshot)

    async def run():
        result = await handoff_sender.attempt(
            daemon, relay, {"room": "build", "message_id": cap.root, "content": "fix task 2"},
            reason="quota", mission={"task_id": "task"}, worktree=wt,
            bindings=reflexd.ROOM_BINDINGS_PATH, **policy())
        if daemon._bg_tasks:
            await asyncio.gather(*list(daemon._bg_tasks))
        return result

    result = asyncio.run(run())
    store = handoff_runtime.journal(daemon, "build", wt)
    events = [json.loads(line) for line in store.events_path.read_text().splitlines()]
    return result, snapshots, events


def test_busy_then_active_checkpoints_and_sends_once_with_original_claim(wiring, monkeypatch):
    relay, delays = Receiver(), []

    async def sleep(delay):
        delays.append(delay)
        assert relay.holder == wiring[1].config.participant_name and not relay.defers
        relay.status = "active"

    monkeypatch.setattr(handoff_sender.asyncio, "sleep", sleep)
    result, snapshots, events = attempt(wiring, monkeypatch, relay)
    assert result == "waiting" and snapshots == [1] and delays == [30]
    assert len(relay.defers) == 1
    assert relay.defers[0]["failover"]["handoffs"] == 1
    selections = [e for e in events if e["event"] == "selection"]
    assert selections[0]["busy_target"] == "b-codex" and selections[0]["target"] is None
    assert selections[1]["target"] == "b-codex"


def test_busy_budget_expires_without_claim_transfer(wiring, monkeypatch):
    relay, delays = Receiver(), []
    clock = [1000.0]
    alerts = []
    monkeypatch.setattr(handoff_wait.time, "time", lambda: clock[0])
    monkeypatch.setattr(handoff_wait, "monotonic", lambda: clock[0])
    monkeypatch.setattr(wiring[1], "_alert_owner", lambda *a: alerts.append(a))

    async def sleep(delay):
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(handoff_sender.asyncio, "sleep", sleep)
    result, snapshots, events = attempt(wiring, monkeypatch, relay)
    assert result == "waiting" and snapshots == [1] and relay.defers == []
    assert relay.holder == wiring[1].config.participant_name
    assert sum(delays) == 900 and delays[:2] == [30, 60]
    assert events[-1]["cause"] == "busy_timeout"
    assert sum(e["event"] == "attempt" for e in events) == 1
    assert len(relay.posted) == 1 and "up to 15 min" in relay.posted[0]["content"]
    assert alerts and "busy_timeout" in alerts[-1][1]


@pytest.mark.parametrize("stop", ["claim", "switch"])
def test_wait_rechecks_claim_and_live_switch(wiring, monkeypatch, stop):
    relay = Receiver()

    async def sleep(delay):
        relay.status = "active"
        if stop == "claim":
            relay.holder = "someone-else"
        else:
            monkeypatch.setattr(handoff_sender, "enabled", lambda *a: False)

    monkeypatch.setattr(handoff_sender.asyncio, "sleep", sleep)
    _, snapshots, events = attempt(wiring, monkeypatch, relay)
    assert snapshots == [1] and relay.defers == []
    assert events[-1]["cause"] == ("no_claim" if stop == "claim" else "gate_closed")


@pytest.mark.parametrize("unavailable", ["offline", "degraded", "nonmember", "zero_bid"])
def test_only_eligible_busy_receivers_cause_a_wait(wiring, monkeypatch, unavailable):
    relay = Receiver()
    if unavailable == "offline":
        relay.online = False
    elif unavailable == "degraded":
        relay.status = "degraded"
    elif unavailable == "nonmember":
        relay.member = False
    else:
        monkeypatch.setattr("tests.test_handoff_selection.policy", lambda: dict(
            detect=lambda n: "claude" if n == "arav-gemini" else "codex",
            capabilities=lambda _: [], score=lambda **_: (0, "no")))

    async def no_sleep(delay):
        pytest.fail("an ineligible receiver must not cause backoff")

    monkeypatch.setattr(handoff_sender.asyncio, "sleep", no_sleep)
    _, snapshots, events = attempt(wiring, monkeypatch, relay)
    assert snapshots == [] and relay.defers == []
    assert events[-1]["cause"] == "no_target"


def test_active_receiver_does_not_wait_for_a_busy_teammate(wiring, monkeypatch):
    relay = Receiver()

    async def presence():
        return [dict(name="b-codex", online=True, status="busy"),
                dict(name="c-codex", online=True, status="active")]

    async def members(**kw):
        return ["b-codex", "c-codex"]

    async def no_sleep(delay):
        pytest.fail("an active receiver should be selected immediately")

    monkeypatch.setattr(relay, "fetch_presence", presence)
    monkeypatch.setattr(relay, "fetch_members", members)
    monkeypatch.setattr(handoff_sender.asyncio, "sleep", no_sleep)
    result, snapshots, _ = attempt(wiring, monkeypatch, relay)
    assert result == "sent" and snapshots == [1]
    assert relay.defers[0]["target"] == "c-codex"


def test_real_checkpoint_and_receiver_proof_after_busy_selection(wiring, monkeypatch, tmp_path):
    repo, sender, wt, _, _, _ = wiring
    relay = Receiver()

    async def sleep(delay):
        assert relay.holder == sender.config.participant_name and not relay.defers
        relay.status = "active"

    monkeypatch.setattr(handoff_sender.asyncio, "sleep", sleep)
    async def run():
        result = await handoff_sender.attempt(
            sender, relay, {"room": "build", "message_id": "real-root", "content": "fix task 2"},
            reason="quota", mission={"task_id": "task"}, worktree=wt,
            bindings=reflexd.ROOM_BINDINGS_PATH, detect=reflexd.detect_harness,
            capabilities=reflexd.capabilities_for, score=reflexd.compute_bid_v2)
        await asyncio.gather(*list(sender._bg_tasks))
        return result

    result = asyncio.run(run())
    assert result == "waiting" and len(relay.defers) == 1
    sent = relay.defers[0]
    assert sent["failover"]["capsule"]["check"]["status"] == "ran"
    receiver = reflexd.Reflexd(reflexd.ReflexdConfig(
        relay_url="http://r", api_key="synthetic", participant_name="b-codex",
        runtime_dir=tmp_path / "receiver"))
    received_wt = reflexd.reflexd_wake.agent_worktree(repo, "b-codex")
    verb = SocialVerb(verb="defer", actor=sender.config.participant_name, room_id="build",
                      ref_message_id="real-root",
                      payload={"to": "b-codex", "failover": sent["failover"]})
    envelope = dict(room="build", from_name=sender.config.participant_name,
                    message_type="social", message_id="typed-delivery",
                    content=verb.model_dump_json())
    decision = asyncio.run(handoff_delivery.receive(
        receiver, relay, envelope, failover.continuation(envelope), received_wt,
        trusted=True, mode="autonomous", bindings=reflexd.ROOM_BINDINGS_PATH))
    assert decision.writable and decision.turn
    assert "reproduced" in relay.posted[-1]["content"]
    assert relay.posted[-1]["message_type"] == "status"
