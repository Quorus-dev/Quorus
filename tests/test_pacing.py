"""`quorus agent pace`: keep an agent inside its owner's plan (2026-10-17).

Night one: Codex burned its 5-hour plan window in ~24 turns. Pacing delays
(never drops) teammate-triggered turns past the cap; humans always go first.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from quorus_cli import agent_cmd

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


def test_pace_wait_math() -> None:
    assert reflexd.pace_wait_s([], 2, 1000) == 0
    assert reflexd.pace_wait_s([100, 200], 3, 1000) == 0
    # Two turns in the last hour at cap 2: wait until the older one ages out.
    assert reflexd.pace_wait_s([100, 200], 2, 1000) == 100 + 3600 - 1000
    assert reflexd.pace_wait_s([-5000, 200], 2, 1000) == 0  # old turn doesn't count


def test_pace_config_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd.Path, "home", staticmethod(lambda: tmp_path))

    class _C:
        def print(self, *_a: Any, **_k: Any) -> None:
            pass

    class _A:
        name, per_hour = "a-codex", "3"

    agent_cmd._pace(_A(), _C())
    path = tmp_path / ".quorus" / "pacing.json"
    assert json.loads(path.read_text()) == {"a-codex": {"per_hour": 3}}
    assert reflexd.pace_for("a-codex", path=path) == 3
    assert reflexd.pace_for("a-claude", path=path) is None
    _A.per_hour = "off"
    agent_cmd._pace(_A(), _C())
    assert reflexd.pace_for("a-codex", path=path) is None


class _Relay:
    def __init__(self) -> None:
        self.posted: list[dict] = []

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": "p"}


def test_over_cap_parks_and_says_so_once_but_humans_go_first(monkeypatch) -> None:
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))
    monkeypatch.setattr(reflexd, "pace_for", lambda name: 1)
    released: list[float] = []

    async def fake_release(relay: Any, wait: float) -> None:
        released.append(wait)

    monkeypatch.setattr(d, "_release_paced", fake_release)
    relay = _Relay()
    agent_msg = {"room": "build", "from_name": "a-claude", "message_id": "m"}

    async def go() -> None:
        assert await d._paced(relay, agent_msg)  # first turn: free
        d._record_turn()  # ...and it ran
        assert not await d._paced(relay, agent_msg)  # second: parked for the slot
        await asyncio.sleep(0)
        assert len(released) == 1 and released[0] > 3500
        assert d._pace_parked["build"][0] is agent_msg
        assert len(relay.posted) == 1
        assert "pacing itself (1 turns/hour" in relay.posted[0]["content"]
        human = {"room": "build", "from_name": "arav", "message_id": "h"}
        assert await d._paced(relay, human)  # boss never waits
    asyncio.run(go())
    assert d._waiting[0] == "pacing"  # the board shows "pacing", not "working"


def test_skipped_messages_use_no_pace_turns(monkeypatch) -> None:
    """Overseer, fix 16: three already-answered envelopes filled the 3/h window
    and the next real message waited an hour."""
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))
    monkeypatch.setattr(reflexd, "pace_for", lambda name: 3)
    d._answered["build"] = {"m1": None, "m2": None, "m3": None}

    async def no_limit(*_a: Any) -> bool:
        return False
    monkeypatch.setattr(d, "_limited", no_limit)
    for mid in ("m1", "m2", "m3"):
        env = {"room": "build", "from_name": "a-claude", "message_id": mid, "id": mid}
        asyncio.run(d._wake_and_reply_inner(_Relay(), env))
    assert d._turns == [] and d._pace_parked == {}


def test_an_owner_mention_during_a_pacing_wait_wakes_at_once(monkeypatch) -> None:
    """The pacing wait held the wake lock, so the owner's direct @mentions sat
    queued for an hour. A parked message no longer holds anything."""
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))
    monkeypatch.setattr(reflexd, "pace_for", lambda name: 1)
    woke: list[str] = []
    gate = asyncio.Event()

    async def fake_sleep(_s: float) -> None:
        await gate.wait()  # the slot opens only when the test says so
    monkeypatch.setattr(d, "_sleep_or_stop", fake_sleep)

    async def fake_wake(relay: Any, env: dict, *, triage: Any = None, **_k: Any) -> None:
        if await d._paced(relay, env, triage):
            d._record_turn()
            woke.append(env["from_name"])

    monkeypatch.setattr(d, "_wake_and_reply", fake_wake)

    async def go() -> None:
        relay = _Relay()
        d._record_turn()  # the hour's one turn is used
        async with d._wake_lock:
            await d._wake_and_reply(relay, {"room": "build", "from_name": "a-claude",
                                            "message_id": "agent"})
        assert woke == [] and not d._wake_lock.locked()
        async def boss() -> None:
            async with d._wake_lock:
                await d._wake_and_reply(relay, {"room": "build", "from_name": "arav",
                                                "message_id": "boss"})
        await asyncio.wait_for(boss(), timeout=2)  # the owner doesn't wait for the slot
        assert woke == ["arav"]
        d._turns = []  # the slot frees up
        gate.set()
        await d._pace_task
        assert woke == ["arav", "a-claude"]  # the parked message still runs
    asyncio.run(go())

