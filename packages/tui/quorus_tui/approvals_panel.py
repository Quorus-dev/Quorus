"""Pending-approval panel for the Quorus TUI (WAKE_REBUILD L3).

An agent blocked on a permission gate is the one thing in a room that
cannot wait for you to scroll: nothing moves until a human answers. So
unlike the work-queue panel, this one is **loud by default** — if anything
is pending it renders expanded, with the exact command to unblock it.

Pure render: :func:`render_approvals_panel` takes the list from
``GET /v1/approvals`` and returns Rich ``Text`` rows. No I/O, no state.
"""
from __future__ import annotations

import time
from typing import Any, Sequence

from rich.text import Text

from .render import INDENT

# Chat is the priority surface — never push the feed off screen.
MAX_VISIBLE_ROWS = 4

# Below this many seconds left, the countdown turns red: the agent is
# about to give up and the wake will be wasted.
URGENT_SECONDS = 60


def _remaining(rec: dict[str, Any]) -> int | None:
    """Whole seconds until this request expires, or None if unknown."""
    expires = rec.get("expires_at")
    if not isinstance(expires, (int, float)):
        return None
    return max(0, int(expires - time.time()))


def _fmt_remaining(seconds: int | None) -> tuple[str, str]:
    """Return ``(text, style)`` for the countdown."""
    if seconds is None:
        return "", "dim"
    if seconds <= 0:
        return "expired", "error"
    if seconds < URGENT_SECONDS:
        return f"{seconds}s left", "error"
    return f"{seconds // 60}m left", "muted"


def _truncate(text: str, width: int) -> str:
    text = " ".join(str(text or "").split())
    if width <= 1 or len(text) <= width:
        return text
    return text[: max(1, width - 1)] + "…"


def render_approvals_panel(
    pending: Sequence[dict[str, Any]],
    *,
    console_width: int = 80,
) -> list[Text]:
    """Rows for every approval waiting on a human. Empty list when none."""
    live = [r for r in pending if r.get("status", "pending") == "pending"]
    if not live:
        return []

    rows: list[Text] = []
    n = len(live)
    header = Text(INDENT)
    header.append("🔐 ", style="")
    header.append("Waiting on you", style="bold warning")
    header.append("  —  ", style="dim")
    header.append(
        f"{n} agent{'s' if n != 1 else ''} blocked", style="warning",
    )
    rows.append(header)

    body_width = max(24, console_width - len(INDENT) - 4)
    for rec in live[:MAX_VISIBLE_ROWS]:
        line = Text(INDENT)
        line.append("  ")
        line.append(str(rec.get("agent", "?")), style="bold primary")
        line.append(" wants ", style="muted")
        line.append(str(rec.get("tool_name", "?")), style="bold")
        left, style = _fmt_remaining(_remaining(rec))
        if left:
            line.append("   ")
            line.append(left, style=style)
        rows.append(line)

        preview = _truncate(rec.get("input_preview", ""), body_width - 6)
        if preview:
            detail = Text(INDENT)
            detail.append("     ")
            detail.append(preview, style="dim")
            rows.append(detail)

        cmd = Text(INDENT)
        cmd.append("     ")
        cmd.append("/approve ", style="accent")
        cmd.append(str(rec.get("id", "")), style="dim")
        cmd.append("   or   ", style="dim")
        cmd.append("/deny ", style="accent")
        cmd.append(str(rec.get("id", "")), style="dim")
        rows.append(cmd)

    if n > MAX_VISIBLE_ROWS:
        more = Text(INDENT)
        more.append("  ")
        more.append(
            f"+{n - MAX_VISIBLE_ROWS} more — see `quorus approvals`",
            style="dim",
        )
        rows.append(more)
    return rows
