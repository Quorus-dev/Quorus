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


def test_over_cap_waits_and_says_so_once_but_humans_go_first(monkeypatch) -> None:
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))
    monkeypatch.setattr(reflexd, "pace_for", lambda name: 1)
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(d, "_sleep_or_stop", fake_sleep)
    relay = _Relay()
    agent_msg = {"room": "build", "from_name": "a-claude", "message_id": "m"}
    assert asyncio.run(d._paced(relay, agent_msg))  # first turn: free
    assert slept == []
    assert asyncio.run(d._paced(relay, agent_msg))  # second: waits for the hour slot
    assert len(slept) == 1 and slept[0] > 3500
    assert len(relay.posted) == 1 and "pacing itself (1 turns/hour" in relay.posted[0]["content"]
    human = {"room": "build", "from_name": "arav", "message_id": "h"}
    slept.clear()
    assert asyncio.run(d._paced(relay, human)) and slept == []  # boss never waits
