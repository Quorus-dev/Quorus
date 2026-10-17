"""Live "who is working" presence and owner alerts (2026-10-17).

Before: the chat went silent between a request and the reply (a working agent
looked dead), and "it's done" never reached a human who had walked away: the
only banner came from an open, idle hub, and only for @-mentions.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from quorus_tui import working

from quorus.notifications import agent_events

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


# ── TUI side ─────────────────────────────────────────────────────────────


def test_tracker_reports_new_workers_and_keeps_start_time() -> None:
    t = working.WorkingTracker()
    assert t.update({"a-claude"}, now=100) == ["a-claude"]
    assert t.update({"a-claude", "a-codex"}, now=130) == ["a-codex"]
    assert t.snapshot() == [("a-claude", 100), ("a-codex", 130)]
    assert t.update(set(), now=140) == [] and t.snapshot() == []


def test_only_busy_online_agents_in_this_room_count() -> None:
    presence = [
        {"name": "a-claude", "online": True, "status": "busy", "room": "build"},
        {"name": "a-codex", "online": True, "status": "busy", "room": "other"},
        {"name": "a-gemini", "online": True, "status": "active", "room": ""},
        {"name": "a-old", "online": False, "status": "busy", "room": "build"},
    ]
    assert working.busy_in_room(presence, "build") == {"a-claude"}


def test_working_line_shows_each_agent_and_elapsed() -> None:
    line = working.render_working_line([("a-claude", 0), ("a-codex", 68)], now=80)
    assert line is not None
    assert line.plain == "  ⋯ @a-claude working 1m 20s · @a-codex working 12s"
    assert working.render_working_line([]) is None


def test_presence_loop_announces_start_but_not_room_switch(monkeypatch) -> None:
    polls = iter([
        [{"name": "a-claude", "online": True, "status": "busy", "room": "build"}],
        [{"name": "a-claude", "online": True, "status": "busy", "room": "build"},
         {"name": "a-codex", "online": True, "status": "busy", "room": "build"}],
    ])
    stop = threading.Event()
    announced: list[str] = []

    def fake_fetch(_relay: str, _headers: dict) -> list:
        try:
            return next(polls)
        except StopIteration:
            stop.set()
            return []

    monkeypatch.setattr(working, "fetch_presence", fake_fetch)
    working.presence_loop("http://r", {}, working.WorkingTracker(), lambda: "build",
                          announced.append, stop, interval=0)
    # a-claude was already working when we opened the room: no announcement.
    assert announced == ["a-codex"]


# ── Daemon side ──────────────────────────────────────────────────────────


class _Relay:
    def __init__(self) -> None:
        self.beats: list[tuple[str, str]] = []

    async def post_heartbeat(self, *, participant: str, status: str, room: str) -> None:
        self.beats.append((status, room))


def _daemon() -> Any:
    cfg = reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                participant_name="arav-claude", spawn_enabled=False)
    return reflexd.Reflexd(cfg)


def test_wake_shows_busy_in_the_room_then_active_even_on_error() -> None:
    d = _daemon()
    relay = _Relay()

    async def boom(*_a: Any, **_k: Any) -> None:
        assert d._presence == ("busy", "build")
        raise RuntimeError("harness exploded")

    d._wake_and_reply_inner = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        asyncio.run(d._wake_and_reply(relay, {"room": "build"}))
    assert relay.beats == [("busy", "build"), ("active", "")]
    assert d._presence == ("active", "")


def test_owner_alert_rules() -> None:
    alert = agent_events.owner_alert
    shipped = "✅ fixed add()\n(quorus) published 1 commit(s) from quorus/a to main"
    assert alert(agent="a-claude", owner="arav", reply=shipped) == (
        "a-claude shipped to main", "✅ fixed add()")
    assert alert(agent="a-claude", owner="arav", reply="@arav which DB?")[0] == (
        "a-claude needs you")
    assert alert(agent="a-claude", owner="arav", reply="⚠ codex: quota")[0] == (
        "a-claude is stuck")
    # Plain chat, a teammate mention, or a longer name never ping.
    for quiet in ("hi! ready to help", "@arav-codex please review", "@aravind hi", "",
                  "@arav here — maya-opencode, idle and ready."):
        assert alert(agent="a-claude", owner="arav", reply=quiet) is None


def test_daemon_pings_owner_when_work_ships(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    monkeypatch.setattr(reflexd, "_notify", lambda t, b, **_k: sent.append((t, b)))
    d = _daemon()
    d._alert_owner("build", "done\n(quorus) published 2 commit(s) from b to main")
    d._alert_owner("build", "just chatting")
    assert sent == [("arav-claude shipped to main · #build", "done")]
    monkeypatch.setenv("QUORUS_NOTIFY", "0")
    d._alert_owner("build", "@arav help")
    assert len(sent) == 1


def test_asks_owner_needs_a_real_ask() -> None:
    ask = agent_events.asks_owner
    assert ask("Done.\n@arav should tokens expire in 1h or 24h?", "arav") == (
        "@arav should tokens expire in 1h or 24h?")
    assert ask("@arav please approve the migration", "arav")
    assert ask("@arav I'm blocked on the API key", "arav")
    assert ask("@arav here, ready", "arav") is None
    assert ask("@arav-claude any blockers?", "arav") is None


def test_lead_telling_owner_its_done_pings() -> None:
    reply = "@arav, both jobs are on main and all 4 tests pass."
    assert agent_events.owner_alert(agent="a-claude", owner="arav", reply=reply) == (
        "a-claude: done", reply)
    assert agent_events.tells_owner_done("@arav-codex done with review", "arav") is None


def test_alerts_name_the_branch_and_ignore_status_reports() -> None:
    """Live 2026-10-17: 'shipped to main' for weekend-branch work, and a
    review report ("@arav: ... reviewed and approved") fired 'needs you'."""
    alert = agent_events.owner_alert
    shipped = "✅ docs\n(quorus) published 1 commit(s) from quorus/a-gemini to weekend/betu"
    assert alert(agent="a-gemini", owner="arav", reply=shipped)[0] == (
        "a-gemini shipped to weekend/betu")
    report = "@arav: `7ebd213d` is reviewed and approved, along with MCP `3eb4f615`."
    assert alert(agent="a-codex", owner="arav", reply=report) is None
    for ask in ("@arav please approve the merge", "@arav I need your approval to push",
                "@arav should I take the legal memo next"):
        assert alert(agent="a-codex", owner="arav", reply=ask)[0] == "a-codex needs you"


def test_stuck_banner_at_most_once_an_hour(monkeypatch) -> None:
    d = _daemon()
    sent: list[str] = []
    monkeypatch.setattr(reflexd, "_notify", lambda title, *a, **k: sent.append(title))
    monkeypatch.setattr(reflexd._agent_events, "enabled", lambda: True)
    for _ in range(4):  # a rate-limit storm: one banner, four room lines
        d._alert_owner("build", "⚠ opencode: the vendor rate-limited this account")
    assert sent == ["arav-claude is stuck · #build"]
    d._stuck_alerted_at -= reflexd.STUCK_ALERT_EVERY_S + 1
    d._alert_owner("build", "⚠ opencode: still limited")
    assert len(sent) == 2
