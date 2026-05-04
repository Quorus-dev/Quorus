"""Tests for the wave-9 wiring of work_queue_panel into hub.py.

The pure-render panel itself already has its own coverage in
``test_work_queue.py`` (relay) and in repository unit tests. These tests
exercise the new HubState toggle + the panel-render smoke surface so a
future regression that orphans the panel again breaks CI.
"""
from __future__ import annotations

import io

from quorus_tui import work_queue_panel
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


def test_ctrl_w_toggles_panel():
    """HubState exposes the Ctrl-W binding's underlying toggle/getter."""
    s = HubState()
    assert s.is_work_queue_panel_open() is False
    assert s.toggle_work_queue_panel() is True
    assert s.is_work_queue_panel_open() is True
    assert s.toggle_work_queue_panel() is False
    assert s.is_work_queue_panel_open() is False


def test_panel_renders_three_active_tasks():
    """Panel renders 3 in-flight rows + a header — proves the orphan
    module is reachable from a hub-style call site."""
    tasks = [
        {
            "task_id": "T-1", "claimed_by": "alice", "status": "in_progress",
            "started_at": "2026-05-03T10:15:00+00:00", "eta_seconds": 120,
        },
        {
            "task_id": "T-2", "claimed_by": "bob", "status": "pending",
            "started_at": "2026-05-03T10:20:00+00:00", "eta_seconds": 60,
        },
        {
            "task_id": "T-3", "claimed_by": "carol", "status": "blocked",
            "started_at": "2026-05-03T10:25:00+00:00", "eta_seconds": 0,
        },
    ]
    rows = work_queue_panel.render_work_queue_panel(
        tasks, expanded=True, console_width=80,
    )
    out = _render_to_str(rows)
    # Three active tasks, three claimants, status table heading present.
    assert "T-1" in out and "T-2" in out and "T-3" in out
    assert "alice" in out and "bob" in out and "carol" in out
    assert "Active work" in out


def test_hub_state_caches_work_queue_tasks():
    """set/get round-trip preserves room + task ordering and returns a
    copy (mutating the returned list must not corrupt internal state)."""
    s = HubState()
    s.set_work_queue_tasks(
        "general",
        [{"task_id": "T-1", "status": "in_progress"}],
        fetched_at=123.0,
    )
    room, tasks, ts = s.get_work_queue()
    assert room == "general"
    assert tasks == [{"task_id": "T-1", "status": "in_progress"}]
    assert ts == 123.0
    tasks.append({"task_id": "intruder"})
    # Returned list is a copy.
    assert len(s.get_work_queue()[1]) == 1
