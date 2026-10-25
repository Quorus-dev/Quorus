"""Limit-status slice 4: the one line each surface shows, and the pre-wall hint.

Design rev 3 rules under test: the vendor's reset is never our retry time; a
guess always has "?"; a Quorus cooldown is never called quota; stale usage
shows its age; no data is "no limit data", never 0%; the hint never counts an
unknown teammate as having room and routes nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import quorus_cli.cli as cli
from httpx import ASGITransport, AsyncClient

from quorus.board import ROWS_JS
from quorus.limit_display import describe, duration, prewall_hints
from quorus.relay import _reset_state, app

NOW = 1_800_000_000.0


def _limit(**kw: Any) -> dict[str, Any]:
    return {"window": "limit", "kind": "observed", "source": "msg.retry_in", "used_pct": None,
            "resets_at": NOW + 2 * 3600 + 600, "seen_at": NOW - 60, **kw}


def _usage(window: str = "5h", pct: float = 72.0, source: str = "claude.stream",
           **kw: Any) -> dict[str, Any]:
    return {"window": window, "kind": "reported", "source": source, "used_pct": pct,
            "resets_at": NOW + 3900, "seen_at": NOW - 120, **kw}


QUOTA = {"reason": "quota", "until": NOW + 1800}


def test_durations_read_like_a_person_wrote_them() -> None:
    assert [duration(s) for s in (5, 60, 45 * 60, 2 * 3600, 2 * 3600 + 600, 3 * 86400 + 4 * 3600,
                                  -10)] == ["<1m", "1m", "45m", "2h", "2h 10m", "3d 4h", "<1m"]


def test_an_observed_limit_shows_the_vendors_reset_and_its_source() -> None:
    line = describe({"pause": QUOTA, "readings": [_limit()]}, NOW)
    assert line == 'out of quota · resets in 2h 10m (vendor said "retry in …")'
    assert "30m" not in line  # our retry time never stands in for the reset


def test_a_guess_or_an_unread_reset_always_carries_a_question_mark() -> None:
    guess = {"window": "limit", "kind": "guess", "source": "msg.no_time", "used_pct": None,
             "resets_at": None, "seen_at": NOW}
    for limits in ({"pause": QUOTA, "readings": [guess]}, {"pause": QUOTA}):
        assert describe(limits, NOW) == "out of quota · retry in about 30m? (no reset time given)"


def test_a_cooldown_is_paused_by_quorus_never_quota() -> None:
    for reason, why in (("slow_start", "slow to start"), ("no_route", "unavailable"),
                        ("free_busy", "free tier limited for this connection or account")):
        line = describe({"pause": {"reason": reason, "until": NOW + 900}}, NOW)
        assert line.startswith("paused by Quorus · retry in 15m") and why in line
        assert "quota" not in line


def test_usage_shows_the_window_nearest_the_wall_and_its_source() -> None:
    line = describe({"readings": [_usage("7d", 40.0), _usage("5h", 72.0)]}, NOW)
    assert line == ("5-hour window 72% used · resets in 1h 05m (Claude Code reported)"
                    " · 7d 40% (Claude Code reported)")
    stale = describe({"readings": [_usage(seen_at=NOW - 20 * 60)]}, NOW)
    assert stale.endswith("(Claude Code reported, 20m ago)")


def test_each_window_carries_its_own_age_and_source() -> None:
    """Codex on 128ec59a: only the top window's age was shown, so a fresh 5h
    80% beside a 30-minute-old 7d 70% read as if both were current."""
    mixed = describe({"readings": [_usage("5h", 80.0),
                                   _usage("7d", 70.0, seen_at=NOW - 30 * 60)]}, NOW)
    assert mixed == ("5-hour window 80% used · resets in 1h 05m (Claude Code reported)"
                     " · 7d 70% (Claude Code reported, 30m ago)")
    # the stale window is the top one: its age is on it, the fresh one has none
    top_stale = describe({"readings": [_usage("5h", 60.0),
                                       _usage("7d", 90.0, seen_at=NOW - 40 * 60)]}, NOW)
    assert top_stale.startswith("7-day window 90% used · resets in 1h 05m "
                                "(Claude Code reported, 40m ago)")
    assert top_stale.endswith(" · 5h 60% (Claude Code reported)")
    # different sources are named per window
    two = describe({"readings": [_usage("5h", 50.0),
                                 _usage("7d", 30.0, source="codex.token_count")]}, NOW)
    assert "5-hour window 50% used" in two and "(Claude Code reported)" in two
    assert two.endswith(" · 7d 30% (Codex reported)")


def test_nothing_known_says_nothing_not_zero() -> None:
    assert describe({}, NOW) == ""


# ── the pre-wall hint ───────────────────────────────────────────────────


def _agent(name: str, room: str = "r", **limits: Any) -> dict[str, Any]:
    return {"name": name, "room": room, "limits": limits}


HOT = _agent("a-claude", readings=[_usage(pct=93.0)])


def test_the_hint_names_a_teammate_on_another_vendor_known_to_have_room() -> None:
    cool = _agent("a-codex", readings=[_usage(pct=20.0, source="codex.token_count"),
                                       _usage("7d", 50.0, source="codex.token_count")])
    assert prewall_hints([HOT, cool], NOW) == {"a-claude": "next task to @a-codex?"}


@pytest.mark.parametrize("other", [
    _agent("a-codex"),  # no limit data: unknown, not room
    _agent("a-codex", pause=QUOTA, readings=[{
        "window": "limit", "kind": "guess", "source": "msg.no_time", "used_pct": None,
        "resets_at": None, "seen_at": NOW}]),  # a guess
    _agent("a-codex", readings=[_usage(pct=20.0, source="codex.token_count",
                                       seen_at=NOW - 30 * 60)]),  # stale
    _agent("a-codex", readings=[_usage(pct=95.0, source="codex.token_count")]),  # near its own
    _agent("a-codex", pause={"reason": "slow_start", "until": NOW + 60},
           readings=[_usage(pct=20.0, source="codex.token_count")]),  # paused
    _agent("a-codex", readings=[_usage(pct=20.0, source="codex.token_count"), _limit()]),
    _agent("b-claude", readings=[_usage(pct=10.0)]),  # same vendor
    _agent("a-codex", room="other", readings=[_usage(pct=10.0, source="codex.token_count")]),
])
def test_the_hint_stays_silent_unless_room_is_known(other: dict[str, Any]) -> None:
    assert prewall_hints([HOT, other], NOW) == {}


def test_no_hint_below_the_line_or_on_stale_usage() -> None:
    cool = _agent("a-codex", readings=[_usage(pct=20.0, source="codex.token_count")])
    warm = _agent("a-claude", readings=[_usage(pct=89.0)])
    old_hot = _agent("a-claude", readings=[_usage(pct=99.0, seen_at=NOW - 30 * 60)])
    assert prewall_hints([warm, cool], NOW) == prewall_hints([old_hot, cool], NOW) == {}


# ── the relay, the board and `quorus ps` ────────────────────────────────

H = {"Authorization": "Bearer test-secret"}


async def test_presence_carries_the_line_and_the_hint() -> None:
    _reset_state()
    now = time.time()
    use = {"window": "5h", "kind": "reported", "used_pct": 95.0, "resets_at": now + 3600,
           "seen_at": now}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/heartbeat", headers=H, json={
            "instance_name": "a-claude", "room": "r",
            "readings": [{**use, "source": "claude.stream"}]})
        await c.post("/heartbeat", headers=H, json={
            "instance_name": "a-codex", "room": "r",
            "readings": [{**use, "source": "codex.token_count", "used_pct": 10.0}]})
        await c.post("/heartbeat", headers=H, json={
            "instance_name": "a-gemini", "room": "r", "waiting_on": "quota",
            "waiting_until": now + 1800, "pause": {"reason": "quota", "until": now + 1800}})
        await c.post("/heartbeat", headers=H, json={"instance_name": "a-opencode", "room": "r"})
        pres = {p["name"]: p for p in (await c.get("/presence", headers=H)).json()}
    _reset_state()
    assert pres["a-claude"]["limit_line"].startswith("5-hour window 95% used")
    assert pres["a-claude"]["limit_hint"] == "next task to @a-codex?"
    gemini = pres["a-gemini"]["limit_line"]  # 29m or 30m, by when the GET lands
    assert gemini.startswith("out of quota · retry in about ") and "m? (no reset" in gemini
    assert "limit_line" not in pres["a-opencode"] and "limit_hint" not in pres["a-codex"]


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_board_rows_use_the_limit_line(tmp_path: Path) -> None:
    now = 1792260000
    presence = {
        "q": {"name": "q", "online": True, "status": "active", "waiting_on": "quota",
              "waiting_until": now + 3660,
              "limit_line": 'out of quota · resets in 1h (vendor said "retry in …")'},
        "c": {"name": "c", "online": True, "status": "active",
              "limit_line": "paused by Quorus · retry in 15m (free model slow to start)"},
        "u": {"name": "u", "online": True, "status": "idle",
              "limit_line": "5-hour window 93% used (Claude Code reported)",
              "limit_hint": "next task to @c?"},
        "n": {"name": "n", "online": True, "status": "idle"},
    }
    (tmp_path / "rows.js").write_text(ROWS_JS)
    script = tmp_path / "run.js"
    script.write_text(
        "globalThis.sessionStorage = { getItem: () => null };\n"
        "const b = require('./rows.js');\n"
        f"const pres = {json.dumps(presence)}, now = {now};\n"
        "const rows = Object.keys(pres).map((n) => b.rowState(n, pres, [], [], now));\n"
        "console.log(JSON.stringify(rows));\n")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=30,
                         cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    rows = {r["name"]: r for r in json.loads(out.stdout)}
    assert rows["q"]["line"] == presence["q"]["limit_line"]  # not "back about <our retry>"
    assert rows["c"]["rank"] == 3 and rows["c"]["line"].startswith("paused by Quorus")
    assert rows["u"]["line"] == ("idle · 5-hour window 93% used (Claude Code reported) "
                                 "→ next task to @c?")
    assert rows["n"]["line"] == "idle"


async def test_quorus_ps_shows_the_limit_line_or_no_limit_data(monkeypatch, capsys) -> None:
    resp = MagicMock(status_code=200, raise_for_status=MagicMock())
    resp.json.return_value = [
        {"name": "a-claude", "online": True, "status": "idle", "room": "r",
         "limit_line": "5-hour window 93% used", "limit_hint": "next task to @a-codex?"},
        {"name": "a-codex", "online": True, "status": "idle", "room": "r"},
        {"name": "b-claude", "online": False, "status": "offline", "room": ""},
    ]
    client = AsyncMock(get=AsyncMock(return_value=resp), aclose=AsyncMock())
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    monkeypatch.setattr(cli.console, "width", 220)
    await cli._ps()
    out = " ".join(capsys.readouterr().out.split())
    assert "LIMITS" in out and "5-hour window 93% used → next task to @a-codex?" in out
    assert "no limit data" in out
