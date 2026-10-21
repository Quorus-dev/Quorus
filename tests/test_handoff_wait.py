"""Long waits are background work; restart and expiry cannot authorize a send."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from quorus.runtime import failover, handoff_runtime, handoff_sender, handoff_wait
from tests.test_handoff_retry import policy
from tests.test_handoff_selection import Receiver, attempt
from tests.test_unfinished_wakes import _git, reflexd

pytest_plugins = ["tests.test_handoff_delivery"]


@pytest.mark.parametrize("status", ["active", "busy"])
def test_future_quorus_pause_excludes_receiver(wiring, monkeypatch, status):
    relay = Receiver()

    async def presence():
        return [dict(name="b-codex", online=True, status=status,
                     pause={"until": time.time() + 900})]

    monkeypatch.setattr(relay, "fetch_presence", presence)
    alerts = []
    monkeypatch.setattr(wiring[1], "_alert_owner", lambda *a: alerts.append(a))
    result, snapshots, events = attempt(wiring, monkeypatch, relay)
    assert result == "stopped" and snapshots == [] and relay.defers == []
    assert events[-1]["cause"] == "no_target" and alerts
    assert not relay.posted  # no chat invitation for an unverified takeover


def test_no_target_wake_alerts_owner_without_posting_an_ambient_takeover_ask(wiring, monkeypatch):
    _, daemon, _, _, _, _ = wiring
    relay = Receiver()
    relay.online = False
    alerts = []
    monkeypatch.setattr(daemon, "_alert_owner", lambda *a: alerts.append(a))

    async def run(*a, **kw):
        return failover.VendorFailure("⚠ quota", "quota")

    monkeypatch.setattr(daemon.adapter, "run", run)
    asyncio.run(daemon._wake_and_reply(relay, dict(room="build", message_id="no-target",
                from_name="arav", message_type="chat", content="finish task 2")))
    assert alerts and any("no_target" in line for _, line in alerts)
    assert not relay.defers
    assert all(m.get("message_type") == "status" for m in relay.posted)


def test_wait_releases_wake_handler_but_fences_other_writes(wiring, monkeypatch):
    _, daemon, wt, _, _, _ = wiring
    relay = Receiver()
    entered = asyncio.Event()

    async def sleeping(delay):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(handoff_wait.asyncio, "sleep", sleeping)
    alerts = []
    monkeypatch.setattr(daemon, "_alert_owner", lambda *a: alerts.append(a))

    async def run():
        async with daemon._wake_lock:
            result = await handoff_sender.attempt(
                daemon, relay, dict(room="build", message_id="background", content="fix"),
                reason="quota", mission={"task_id": "task"}, worktree=wt,
                bindings=reflexd.ROOM_BINDINGS_PATH, **policy())
        assert result == "waiting"
        await entered.wait()
        assert not daemon._wake_lock.locked()
        async with daemon._wake_lock:  # another wake can acquire it immediately
            assert handoff_runtime.blocked(daemon, "build", wt)
            assert handoff_runtime.skip_sync(daemon, "other-room", wt)
        store = handoff_runtime.journal(daemon, "build", wt)
        entry = next(iter(store._load().values()))
        assert entry["state"] == "waiting" and entry["target"] == "b-codex"
        assert 890 < entry["deadline"] - time.time() <= 900
        assert len(relay.posted) == 1 and "up to 15 min" in relay.posted[0]["content"]
        assert relay.holder == daemon.config.participant_name and not relay.defers
        task = next(iter(daemon._bg_tasks))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert next(iter(store._load().values()))["cause"] == "interrupted"
        assert alerts and "interrupted" in alerts[-1][1]

    asyncio.run(run())


def test_restart_marks_wait_interrupted_and_report_reads_it(wiring, monkeypatch):
    from scripts.handoff_report import load, rows

    _, daemon, wt, cap, _, _ = wiring
    store = handoff_runtime.journal(daemon, "build", wt)
    store.event(dict(event="attempt", attempt_id="restart", room="build"))
    with store.locked():
        store.write(cap, "waiting", snapshot=cap.model_dump(), target="b-codex",
                    deadline=time.time() + 900, attempt_id="restart", data={})
    alerts = []
    monkeypatch.setattr(daemon, "_alert_owner", lambda *a: alerts.append(a))
    handoff_wait.interrupt_pending(daemon)
    assert store.read(cap)["state"] == "failed"
    assert store.read(cap)["cause"] == "interrupted" and alerts
    events = [json.loads(line) for line in store.events_path.read_text().splitlines()]
    assert rows(events, {}, {})[0]["result"] == "interrupted"
    table = rows(*load([daemon.config.runtime_dir.parent]))
    assert table[0]["result"] == "interrupted" and "receivers" not in table[0]
    count = len(events)
    handoff_wait.interrupt_pending(daemon)
    assert len(store.events_path.read_text().splitlines()) == count  # boot is idempotent
    store.events_path.write_text(json.dumps(events[0]) + "\n")  # lost terminal event
    assert rows(*load([daemon.config.runtime_dir.parent]))[0]["result"] == "interrupted"


def test_head_moving_during_wait_never_sends_the_old_proof(wiring, monkeypatch):
    repo, _, wt, _, _, _ = wiring
    relay = Receiver()

    async def sleep(delay):
        (wt.path / "CHANGELOG.md").write_text("changed while waiting\n")
        _git(wt.path, "commit", "-qam", "concurrent edit")
        relay.status = "active"

    monkeypatch.setattr(handoff_wait.asyncio, "sleep", sleep)
    _, snapshots, events = attempt(wiring, monkeypatch, relay)
    assert snapshots == [1] and relay.defers == []
    assert events[-1]["cause"] == "stale"
    assert _git(repo, "rev-parse", "main") != _git(wt.path, "rev-parse", "HEAD")


@pytest.mark.parametrize("minutes", [1, 15, 30, 60])
def test_room_wait_cap_is_owner_configurable(tmp_path, minutes):
    from quorus.runtime.check_config import configure_binding

    entry = configure_binding({"path": str(tmp_path)}, None,
                              SimpleNamespace(handoff_wait_minutes=minutes), tmp_path)
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps({"build": entry}))
    assert handoff_runtime.wait_seconds(path, "build") == minutes * 60


@pytest.mark.parametrize("minutes", [0, 61, True, 10**400, float("nan")])
def test_invalid_room_wait_cap_is_refused(tmp_path, minutes):
    from quorus.runtime.check_config import configure_binding

    with pytest.raises(ValueError, match="handoff-wait-minutes"):
        configure_binding(str(tmp_path), None,
                          SimpleNamespace(handoff_wait_minutes=minutes), tmp_path)
