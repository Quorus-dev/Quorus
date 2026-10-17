"""Silence must be visible (Arav, 2026-10-17): a user can't tell an agent that
chose not to reply, or agents paused by the loop guard, from a dead agent."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

from quorus_tui.chat import render_bubble_feed

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


class _Relay:
    def __init__(self) -> None:
        self.posted: list[dict] = []

    async def fetch_recent(self, *, room: str, limit: int = 10):
        return []

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": "p1"}


def test_no_reply_leaves_a_quiet_status_line(monkeypatch) -> None:
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))

    async def fake_run(harness, **_kw: Any) -> str:
        return "NO_REPLY"

    monkeypatch.setattr(d.adapter, "run", fake_run)
    relay = _Relay()
    env = {"id": "e", "message_id": "m1", "room": "build", "from_name": "a-claude",
           "content": "thanks @a-codex", "message_type": "chat"}
    asyncio.run(d._wake_and_reply(relay, env, reason="t"))
    assert relay.posted == [{"room": "build", "from_name": "a-codex",
                             "content": "@a-codex read it, nothing to add",
                             "message_type": "status", "reply_to": "m1"}]
    # A status line never wakes anyone.
    res = reflexd.classify_message(content="@a-claude read it", sender="a-codex",
                                   self_name="a-claude", message_type="status")
    assert res.action == "IGNORE"


def test_pause_is_announced_once_per_pause() -> None:
    human = {"from_name": "arav", "content": "go"}
    agent = {"from_name": "a-claude", "content": "done"}
    notice = {"from_name": "a-codex", "content": f"{reflexd.PAUSE_NOTICE} after 6"}
    assert not reflexd._pause_announced([human, agent, agent])
    assert reflexd._pause_announced([human, agent, notice, agent])
    # A new human message starts a new pause cycle.
    assert not reflexd._pause_announced([notice, human, agent])


def test_status_renders_as_a_quiet_system_line() -> None:
    msg = {"from_name": "a-codex", "content": "@a-codex read it, nothing to add",
           "message_type": "status", "timestamp": "2026-10-17T10:00:00Z"}
    rows = [r.plain for r in render_bubble_feed([msg], "build", "arav", console_width=100)]
    assert any("read it, nothing to add" in r for r in rows)
    assert not any(r.strip().startswith("╭") for r in rows)  # not a chat bubble
