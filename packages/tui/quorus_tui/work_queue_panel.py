"""Stream B work-queue panel for the Quorus TUI.

A fold-up panel rendered above the room feed when the room has any
in-flight tasks. Two states:

* **Collapsed** (default): a single line —
  ``Active work — N tasks · Ctrl-W to expand``.
* **Expanded** (after Ctrl-W toggle): a Rich-friendly table with one row
  per task: ``task_id, claimed_by, status, started_at, eta``.

Pure render — :func:`render_work_queue_panel` accepts a list of task
dicts (as returned by ``GET /v1/work_queue/{room_id}``) and a boolean
``expanded`` flag. No I/O, no global state.

The panel is intentionally lightweight: in-flight tasks are usually 0-5
in normal operation, and the expanded view has to fit ABOVE the feed
without pushing chat off screen. So we cap to MAX_VISIBLE_ROWS and add a
``+N more`` line when truncated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from rich.text import Text

from .render import INDENT

# Hard cap on rows in the expanded view. Chat is the priority surface;
# the work-queue should never push the bubble feed off the bottom of
# the terminal.
MAX_VISIBLE_ROWS = 6

# Emoji-free status pills. Mapping intentionally short — anything else
# falls through to the raw status string.
_STATUS_GLYPH: dict[str, str] = {
    "pending": "○",
    "in_progress": "▶",
    "blocked": "⊘",
    "done": "✓",
    "failed": "✗",
    "cancelled": "·",
}

# L27: lifted to a module-level frozenset so the comprehension below
# does a single ``in`` lookup per task instead of re-allocating a set
# literal on every iteration. Frozenset makes accidental mutation a
# TypeError.
_ACTIVE_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "blocked"})


def _pluralise(count: int, singular: str) -> str:
    return singular if count == 1 else singular + "s"


def _short_iso(ts: str) -> str:
    """Render a wall-clock-ish timestamp from an ISO 8601 string.

    ``2026-05-02T15:42:09+00:00`` → ``15:42``. We deliberately strip the
    date because the expanded view has limited horizontal real estate;
    operators care about "how long has this been claimed" more than the
    absolute date.
    """
    if not ts:
        return ""
    try:
        normalized = ts.replace("Z", "+00:00")
        d = datetime.fromisoformat(normalized)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    return d.strftime("%H:%M")


def _eta_str(eta_seconds: int) -> str:
    """Compact ETA — ``5m``, ``45s``, or ``-`` when zero."""
    if not eta_seconds:
        return "-"
    if eta_seconds < 60:
        return f"{eta_seconds}s"
    minutes = eta_seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "…"


def render_work_queue_panel(
    tasks: Sequence[dict[str, Any]],
    *,
    expanded: bool = False,
    console_width: int = 80,
) -> list[Text]:
    """Return the rendered panel rows.

    *tasks* is the list as returned by the relay. Tasks with status
    ``done``/``failed``/``cancelled`` are filtered out before rendering;
    the panel is for *active* work. Returns an empty list when there
    are no active tasks (the caller should not render the panel header).
    """
    if not tasks:
        return []

    active = [t for t in tasks if t.get("status") in _ACTIVE_STATUSES]
    if not active:
        return []

    n = len(active)
    if not expanded:
        # Collapsed: a single dim line.
        line = Text(INDENT)
        line.append("◑ ", style="muted")
        line.append("Active work", style="bold primary")
        line.append("  —  ", style="dim")
        line.append(f"{n} {_pluralise(n, 'task')}", style="muted")
        line.append("   ", style="")
        line.append("(Ctrl-W to expand)", style="dim italic")
        return [line]

    # Expanded — table rows. Manually laid out via str.ljust so we never
    # depend on terminal Rich.Table widths.
    rows: list[Text] = []
    head = Text(INDENT)
    head.append("◑ ", style="muted")
    head.append("Active work", style="bold primary")
    head.append("  —  ", style="dim")
    head.append(f"{n} {_pluralise(n, 'task')}", style="muted")
    head.append("   ", style="")
    head.append("(Ctrl-W to collapse)", style="dim italic")
    rows.append(head)

    # Column widths chosen to fit comfortably in 80-cell terminals; for
    # narrower windows we still render but values may wrap visually.
    task_w = max(8, min(20, (console_width - 30) // 4))
    actor_w = max(8, min(20, (console_width - 30) // 4))
    status_w = 12
    started_w = 6
    eta_w = 5

    header = Text(INDENT)
    header.append(_truncate("task_id", task_w).ljust(task_w), style="dim")
    header.append("  ")
    header.append(_truncate("claimed_by", actor_w).ljust(actor_w), style="dim")
    header.append("  ")
    header.append("status".ljust(status_w), style="dim")
    header.append("  ")
    header.append("started".ljust(started_w), style="dim")
    header.append("  ")
    header.append("eta".ljust(eta_w), style="dim")
    rows.append(header)

    visible = active[:MAX_VISIBLE_ROWS]
    for task in visible:
        status = str(task.get("status") or "")
        glyph = _STATUS_GLYPH.get(status, "·")
        actor = task.get("claimed_by") or task.get("requested_by") or "-"
        started = _short_iso(task.get("started_at") or "")
        eta = _eta_str(int(task.get("eta_seconds") or 0))

        row = Text(INDENT)
        row.append(
            _truncate(task.get("task_id", ""), task_w).ljust(task_w),
            style="bright",
        )
        row.append("  ")
        row.append(
            _truncate(str(actor), actor_w).ljust(actor_w),
            style="muted",
        )
        row.append("  ")
        row.append(f"{glyph} ", style="primary")
        row.append(_truncate(status, status_w - 2).ljust(status_w - 2))
        row.append("  ")
        row.append(started.ljust(started_w), style="muted")
        row.append("  ")
        row.append(eta.ljust(eta_w), style="muted")
        rows.append(row)

    if len(active) > MAX_VISIBLE_ROWS:
        more = len(active) - MAX_VISIBLE_ROWS
        tail = Text(INDENT)
        tail.append(f"  +{more} more", style="dim italic")
        rows.append(tail)

    return rows


def panel_summary(tasks: Sequence[dict[str, Any]]) -> str:
    """One-line text summary used by Hub status-bar / OS notifications."""
    # L27: single-pass count over a frozenset membership check; avoids
    # the list-then-len round trip the previous implementation did.
    n = sum(1 for t in tasks if t.get("status") in _ACTIVE_STATUSES)
    if n == 0:
        return ""
    return f"Active work — {n} {_pluralise(n, 'task')}"


__all__ = [
    "MAX_VISIBLE_ROWS",
    "panel_summary",
    "render_work_queue_panel",
]
