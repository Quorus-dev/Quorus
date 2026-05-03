"""Tests for ``packages/cli/quorus_cli/reflex_status.py``.

The dashboard is a thin formatter over disk state. We test by feeding
fabricated state dicts directly to ``render_table`` / ``render_json``,
and by pointing ``collect_state`` at temporary directories. No real
reflexd processes spawn during these tests.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from quorus_cli.reflex_status import (
    cmd_reflex_status,
    collect_state,
    render_json,
    render_table,
)
from rich.console import Console


def _render_to_str(state):
    """Capture a Rich table to plain text for substring assertions."""
    buf = io.StringIO()
    console = Console(file=buf, width=160, force_terminal=False, color_system=None)
    console.print(render_table(state))
    return buf.getvalue()


def _empty_state(*, tmp_path: Path) -> dict:
    """Helper — minimal but well-formed state dict."""
    return {
        "agents": [],
        "binaries": {"claude": None, "codex": None, "gemini": None, "cursor": None},
        "log_path": str(tmp_path / "reflexd.log"),
        "log_present": False,
        "collected_at": "2026-05-02T12:00:00+00:00",
    }


# ── 1. table renders for 0 / 1 / N agents ───────────────────────────────────


class TestRenderTableShape:
    def test_empty_state_shows_help_hint(self, tmp_path: Path):
        state = _empty_state(tmp_path=tmp_path)
        out = _render_to_str(state)
        assert "Quorus Reflex" in out
        assert "No reflexd pidfiles" in out
        assert "quorus reflexd start" in out

    def test_single_agent_table(self, tmp_path: Path):
        state = _empty_state(tmp_path=tmp_path)
        state["binaries"]["codex"] = "/usr/local/bin/codex"
        state["agents"] = [{
            "participant": "arav-codex",
            "pid": 12345,
            "online": True,
            "uptime_seconds": 73.0,
            "sse_state": "connected",
            "last_wake": "5s",
            "last_error": "—",
            "harness_cli": "codex",
            "room_count": 2,
            "last_bids": [],
            "active_claims": [],
        }]
        out = _render_to_str(state)
        assert "arav-codex" in out
        assert "online" in out
        assert "connected" in out
        assert "1.2m" in out  # 73 seconds humanized
        assert "codex" in out

    def test_multiple_agents_each_row(self, tmp_path: Path):
        state = _empty_state(tmp_path=tmp_path)
        state["binaries"]["claude"] = "/usr/bin/claude"
        state["binaries"]["codex"] = "/usr/bin/codex"
        state["agents"] = [
            {"participant": f"arav-{h}", "pid": 1000 + i, "online": (i % 2 == 0),
             "uptime_seconds": 10.0 * i, "sse_state": "connected", "last_wake": "—",
             "last_error": "—", "harness_cli": h, "room_count": i,
             "last_bids": [], "active_claims": []}
            for i, h in enumerate(("claude", "codex", "gemini"))
        ]
        out = _render_to_str(state)
        for h in ("claude", "codex", "gemini"):
            assert f"arav-{h}" in out


# ── 2. --json output is parseable ───────────────────────────────────────────


class TestJsonRender:
    def test_json_round_trips(self, tmp_path: Path):
        state = _empty_state(tmp_path=tmp_path)
        state["agents"] = [{
            "participant": "arav-codex", "pid": 1, "online": False,
            "uptime_seconds": None, "sse_state": "?", "last_wake": "—",
            "last_error": "—", "harness_cli": "codex", "room_count": 0,
            "last_bids": [], "active_claims": [],
        }]
        text = render_json(state)
        parsed = json.loads(text)
        assert parsed["agents"][0]["participant"] == "arav-codex"
        assert "binaries" in parsed
        # sort_keys=True keeps the schema deterministic for diffing.
        assert text.index("agents") < text.index("binaries")


# ── 3. --watch terminates cleanly on KeyboardInterrupt ──────────────────────


class TestWatchLoop:
    def test_watch_loop_exits_on_keyboard_interrupt(self, tmp_path: Path,
                                                    monkeypatch):
        """Simulate Ctrl-C — the loop must catch and return without raising."""

        # Fake args: --watch with a tiny interval; collect_state stubbed to
        # raise KeyboardInterrupt immediately so we never block on sleep.
        class Args:
            json = False
            watch = True
            interval = 1

        from quorus_cli import reflex_status as rs

        def boom(*_a, **_k):
            raise KeyboardInterrupt

        monkeypatch.setattr(rs, "collect_state", boom)
        # Should NOT raise — handler swallows KeyboardInterrupt.
        rs.cmd_reflex_status(Args())

    def test_watch_and_json_mutually_exclusive(self, capsys):
        class Args:
            json = True
            watch = True
            interval = 1
        with pytest.raises(SystemExit) as exc_info:
            cmd_reflex_status(Args())
        assert exc_info.value.code == 2


# ── 4. missing pidfile → agent shown as offline ─────────────────────────────


class TestPidfileLiveness:
    def test_dead_pid_marked_offline(self, tmp_path: Path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        # Pick a definitely-dead PID (a giant integer that won't exist).
        (runtime / "reflexd.zombie-bot.pid").write_text("9999999\n")
        state = collect_state(runtime_dir=runtime, log_path=tmp_path / "missing.log")
        agents = state["agents"]
        assert len(agents) == 1
        assert agents[0]["participant"] == "zombie-bot"
        assert agents[0]["online"] is False

    def test_live_pid_marked_online(self, tmp_path: Path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        # Use our own PID — guaranteed alive while the test runs.
        (runtime / "reflexd.self-test.pid").write_text(str(os.getpid()))
        state = collect_state(runtime_dir=runtime, log_path=tmp_path / "missing.log")
        agents = state["agents"]
        assert len(agents) == 1
        assert agents[0]["online"] is True

    def test_malformed_pidfile_treated_as_offline(self, tmp_path: Path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        (runtime / "reflexd.broken.pid").write_text("not-a-number\n")
        state = collect_state(runtime_dir=runtime, log_path=tmp_path / "missing.log")
        assert state["agents"][0]["pid"] is None
        assert state["agents"][0]["online"] is False


# ── 5. log tail surfaces last error ─────────────────────────────────────────


class TestLogParsing:
    def test_log_tail_extracts_error(self, tmp_path: Path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        (runtime / "reflexd.arav-codex.pid").write_text(str(os.getpid()))

        log = tmp_path / "reflexd.log"
        log.write_text(
            "2026-05-01T10:00:00 arav-codex sse connected\n"
            "2026-05-01T10:01:00 arav-codex posted reply to msg-1\n"
            "2026-05-01T10:02:00 arav-codex ERROR auth refresh failed\n"
        )
        state = collect_state(runtime_dir=runtime, log_path=log)
        a = state["agents"][0]
        assert a["sse_state"] == "connected"
        assert "auth refresh failed" in a["last_error"]

    def test_missing_log_yields_unknowns(self, tmp_path: Path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        (runtime / "reflexd.arav-codex.pid").write_text(str(os.getpid()))
        state = collect_state(runtime_dir=runtime, log_path=tmp_path / "no.log")
        a = state["agents"][0]
        assert a["sse_state"] == "?"
        assert a["last_wake"] == "—"
        assert a["last_error"] == "—"


# ── 6. harness CLI detection ────────────────────────────────────────────────


class TestHarnessDetection:
    def test_known_suffix_inferred(self, tmp_path: Path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        for h in ("claude", "codex", "gemini", "cursor"):
            (runtime / f"reflexd.team-{h}.pid").write_text("0")
        state = collect_state(runtime_dir=runtime, log_path=tmp_path / "no.log")
        seen = {a["participant"]: a["harness_cli"] for a in state["agents"]}
        assert seen == {f"team-{h}": h for h in ("claude", "codex", "gemini", "cursor")}

    def test_unknown_suffix_falls_back_to_dash(self, tmp_path: Path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        (runtime / "reflexd.standalone.pid").write_text("0")
        state = collect_state(runtime_dir=runtime, log_path=tmp_path / "no.log")
        assert state["agents"][0]["harness_cli"] == "—"

    def test_binaries_detected_via_shutil_which(self, tmp_path: Path):
        # Just assert the keys exist; values depend on the host PATH.
        state = collect_state(
            runtime_dir=tmp_path / "no_runtime",
            log_path=tmp_path / "no.log",
        )
        for h in ("claude", "codex", "gemini", "cursor"):
            assert h in state["binaries"]


# ── 7. CLI smoke (non-watch) executes without raising ────────────────────────


class TestCliSmoke:
    def test_status_prints_table_no_args(self, capsys, monkeypatch, tmp_path: Path):
        """The default `quorus reflex status` path: no flags, no watch."""

        class Args:
            json = False
            watch = False
            interval = 2

        from quorus_cli import reflex_status as rs

        # Force collect_state into a tmp dir to avoid touching the real
        # ~/.quorus on the developer machine running pytest.
        def _fake(**_kw):
            return _empty_state(tmp_path=tmp_path)

        monkeypatch.setattr(rs, "collect_state", _fake)
        rs.cmd_reflex_status(Args())
        out = capsys.readouterr().out
        # Rich table heading should appear in the captured stream.
        assert "Quorus Reflex" in out

    def test_status_json_emits_valid_json(self, capsys, monkeypatch, tmp_path: Path):
        class Args:
            json = True
            watch = False
            interval = 2

        from quorus_cli import reflex_status as rs

        def _fake(**_kw):
            return _empty_state(tmp_path=tmp_path)

        monkeypatch.setattr(rs, "collect_state", _fake)
        rs.cmd_reflex_status(Args())
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["agents"] == []


# ── 8. M18 — single-pass log parser fans out signals to every agent ────────


class TestSinglePassLogFanout:
    """M18 — _parse_log_fanout walks the log ONCE for N agents.

    The pre-fix path called ``_parse_log_for(p, lines)`` per agent,
    making the cost O(N · log_size). The fix consolidates this into one
    reverse-scan of the log that fans signals out via dict-of-dicts.
    """

    def test_single_pass_fans_signals_per_agent(self):
        from quorus_cli import reflex_status as rs

        lines = [
            "2026-05-01T10:00:00 arav-codex sse connected",
            "2026-05-01T10:00:05 arav-claude sse connected",
            "2026-05-01T10:01:00 arav-codex posted reply to msg-1",
            "2026-05-01T10:01:30 arav-claude WARNING flaky route",
            "2026-05-01T10:02:00 arav-codex ERROR token refresh failed",
            "2026-05-01T10:02:30 arav-gemini sse disconnected",
        ]
        out = rs._parse_log_fanout(
            lines, ["arav-codex", "arav-claude", "arav-gemini"],
        )
        assert out["arav-codex"]["sse_state"] == "connected"
        assert "token refresh failed" in out["arav-codex"]["last_error"]
        assert out["arav-claude"]["sse_state"] == "connected"
        # last_error captures any "warning" hit too.
        assert "flaky route" in out["arav-claude"]["last_error"].lower()
        assert out["arav-gemini"]["sse_state"] == "disconnected"

    def test_fanout_returns_default_for_unmentioned_participant(self):
        from quorus_cli import reflex_status as rs

        out = rs._parse_log_fanout(
            ["2026-05-01T10:00:00 arav-codex sse connected"],
            ["unmentioned-agent"],
        )
        sig = out["unmentioned-agent"]
        assert sig["sse_state"] == "?"
        assert sig["last_error"] == "—"
        assert sig["last_wake"] == "—"
        assert sig["last_bids"] == []

    def test_fanout_matches_legacy_parse_log_for_per_agent(self):
        """Behaviour parity — fanout output for one agent equals legacy
        _parse_log_for output. Guards against subtle drift."""
        from quorus_cli import reflex_status as rs

        lines = [
            "2026-05-01T10:00:00 arav-codex sse connected",
            "2026-05-01T10:01:00 arav-codex posted reply",
            "2026-05-01T10:02:00 arav-codex ERROR boom",
        ]
        legacy = rs._parse_log_for("arav-codex", lines)
        fan = rs._parse_log_fanout(lines, ["arav-codex"])["arav-codex"]
        # Same shape and same fields. last_wake is humanized so it
        # depends on time.time(); skip equality there.
        assert fan["sse_state"] == legacy["sse_state"]
        assert fan["last_error"] == legacy["last_error"]
        assert fan["last_bids"] == legacy["last_bids"]

    def test_collect_state_uses_single_pass_for_multi_agent(self, tmp_path: Path):
        """Smoke test — multi-agent collect_state pulls signals for each
        from one log read. Counts the number of times the fanout helper
        is invoked to prove we're not back to per-agent loops."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        for name in ("arav-codex", "arav-claude", "arav-gemini"):
            (runtime / f"reflexd.{name}.pid").write_text(str(os.getpid()))

        log = tmp_path / "reflexd.log"
        log.write_text(
            "2026-05-01T10:00:00 arav-codex sse connected\n"
            "2026-05-01T10:00:00 arav-claude sse disconnected\n"
            "2026-05-01T10:00:00 arav-gemini sse connected\n",
        )

        from quorus_cli import reflex_status as rs

        call_count = {"n": 0}
        original = rs._parse_log_fanout

        def counting(lines, participants):
            call_count["n"] += 1
            return original(lines, participants)

        # Patch on the module so collect_state uses the wrapper.
        rs._parse_log_fanout = counting
        try:
            state = rs.collect_state(runtime_dir=runtime, log_path=log)
        finally:
            rs._parse_log_fanout = original

        # Exactly ONE fanout call regardless of agent count.
        assert call_count["n"] == 1
        sse_by_agent = {a["participant"]: a["sse_state"] for a in state["agents"]}
        assert sse_by_agent["arav-codex"] == "connected"
        assert sse_by_agent["arav-claude"] == "disconnected"
        assert sse_by_agent["arav-gemini"] == "connected"
