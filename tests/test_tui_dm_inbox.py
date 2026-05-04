"""Tests for the wave-9 wiring of dm_inbox into hub.py.

Covers the new HubState toggle + the SSE plumbing that appends agent_dm
events into the bounded deque. We do NOT spin up a real relay here —
the dispatch parser is exercised directly via the SSE-line shape that
``_dm_sse_loop`` handles.
"""
from __future__ import annotations

import io
import json

from quorus_tui import dm_inbox
from quorus_tui.hub import HubState
from rich.console import Console


def _render_to_str(lines, *, width: int = 80) -> str:
    buf = io.StringIO()
    console = Console(
        file=buf, width=width, force_terminal=False,
        no_color=True, legacy_windows=False,
    )
    for line in lines:
        console.print(line)
    return buf.getvalue()


def test_ctrl_d_toggles_inbox():
    """HubState exposes the toggle for the inbox-panel binding.

    Wave-9 used Ctrl-N (\\x0e) instead of Ctrl-D because Ctrl-D already
    binds to the QUIT sentinel in _read_input — see CHANGELOG. The
    state-shape test below is identical regardless of which key is
    wired in the input loop.
    """
    s = HubState()
    assert s.is_dm_inbox_open() is False
    assert s.toggle_dm_inbox() is True
    assert s.is_dm_inbox_open() is True
    assert s.toggle_dm_inbox() is False
    assert s.is_dm_inbox_open() is False


def test_dm_messages_appended_from_sse():
    """append_dm_message is the contract _dm_sse_loop calls per agent_dm
    event. The deque is bounded — older items evict to keep the panel
    snappy without a runaway memory profile."""
    s = HubState()
    for i in range(5):
        s.append_dm_message({
            "from": f"agent-{i}",
            "to": "arav",
            "content": f"hi {i}",
            "ts": "2026-05-03T10:00:00+00:00",
            "metadata": {"room": "general"},
        })
    msgs = s.get_dm_messages()
    assert len(msgs) == 5
    assert msgs[0]["from"] == "agent-0"
    assert msgs[-1]["from"] == "agent-4"
    # The deque is independent — mutating the returned list must not
    # corrupt internal state.
    msgs.append({"from": "intruder"})
    assert len(s.get_dm_messages()) == 5


def test_dm_panel_renders_envelopes():
    """Smoke render — proves the orphan module is reachable from a hub
    call site once the wiring lands."""
    envelopes = [
        {
            "from": "claude-bot",
            "content": "PR ready for review — see #pr-1234",
            "ts": "2026-05-03T10:00:00+00:00",
            "metadata": {"room": "engineering"},
        },
        {
            "from": "gemini-bot",
            "content": "spec updated",
            "ts": "2026-05-03T10:05:00+00:00",
            "metadata": {"room": "design"},
        },
    ]
    rows = dm_inbox.render_dm_inbox_panel(envelopes, console_width=100)
    out = _render_to_str(rows, width=100)
    assert "@claude-bot" in out
    assert "@gemini-bot" in out
    assert "Agent DMs" in out


def test_dm_sse_loop_parses_agent_dm_events(monkeypatch):
    """Replay an SSE-shaped script through _dm_sse_loop and confirm it
    appends agent_dm events to HubState. No relay is required."""
    from quorus_tui import hub

    state = HubState()
    envelope = json.dumps({"from": "alice-codex", "content": "ping",
                           "ts": "2026-05-03T10:00:00+00:00"})

    class _R:
        status_code = 200

        def iter_lines(self):
            yield "event: agent_dm"
            yield f"data: {envelope}"
            yield ""

        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(hub.httpx, "stream", lambda *a, **k: _R())
    monkeypatch.setattr(hub, "_mint_sse_token", lambda *a: "tok")

    import threading
    import time
    stop = threading.Event()
    t = threading.Thread(
        target=hub._dm_sse_loop,
        args=("http://r", "s", "arav", state, stop),
        daemon=True,
    )
    t.start()
    for _ in range(20):
        if state.get_dm_messages():
            break
        time.sleep(0.05)
    stop.set()
    t.join(timeout=1)

    msgs = state.get_dm_messages()
    assert msgs and msgs[0]["from"] == "alice-codex"
