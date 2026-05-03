"""Stream B agent-DM inbox panel for the Quorus TUI.

Renders the per-participant agent DM inbox as an iMessage-style list:

  ┌─────────────────────────────────────┐
  │  @sender (#room) · 2m ago           │
  │   ↳ "first 60 chars of the body…"   │
  └─────────────────────────────────────┘

This is a *separate* surface from the human DM inbox (``backends.messages``)
so agent-to-agent chatter doesn't pollute the user's notification feed.
The relay endpoint is ``GET /v1/dm/{participant}`` and the SSE stream
is ``/stream/dm/{participant}``.

Pure render — :func:`render_dm_inbox_panel` accepts a list of envelope
dicts and returns Rich ``Text`` rows. No I/O, no global state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from rich.text import Text

from .render import INDENT

# Cap how many DMs render at once. Older messages stay in the inbox; this
# is just a "what's new" preview — the user can scroll the actual room
# feed for the canonical history.
MAX_VISIBLE = 8

# Body preview width — single-line, snappy.
PREVIEW_CHARS = 80


def _short_relative(iso_ts: str) -> str:
    """Render ``2m ago``-style. Returns ``""`` when unparseable."""
    if not iso_ts:
        return ""
    try:
        normalized = iso_ts.replace("Z", "+00:00")
        ts = datetime.fromisoformat(normalized)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return ""
    if secs < 45:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86_400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86_400}d ago"


def _preview(content: str) -> str:
    if not content:
        return ""
    flat = content.replace("\n", " ").replace("\r", " ").strip()
    if len(flat) <= PREVIEW_CHARS:
        return flat
    return flat[: PREVIEW_CHARS - 1] + "…"


def render_dm_inbox_panel(
    messages: Sequence[dict[str, Any]],
    *,
    console_width: int = 80,
    show_room: bool = True,
) -> list[Text]:
    """Render the inbox panel rows.

    *messages* is the list returned by ``GET /v1/dm/{participant}``.
    Newest last (chrono); we display them newest-first since "what's
    new" is the more useful read for the panel.

    Returns an empty list when *messages* is empty so the caller can
    skip the panel header entirely.
    """
    if not messages:
        return []

    # Reverse-chrono: newest at top of the panel.
    items = list(reversed(messages[-MAX_VISIBLE:]))
    rows: list[Text] = []

    head = Text(INDENT)
    head.append("✉ ", style="muted")
    head.append("Agent DMs", style="bold primary")
    head.append("  —  ", style="dim")
    label = "message" if len(messages) == 1 else "messages"
    head.append(f"{len(messages)} {label}", style="muted")
    rows.append(head)

    for env in items:
        sender = env.get("from") or env.get("from_name") or "?"
        room = env.get("metadata", {}).get("room") if isinstance(
            env.get("metadata"), dict
        ) else None
        ts = env.get("ts") or env.get("timestamp") or ""
        relative = _short_relative(ts)
        preview = _preview(str(env.get("content") or ""))

        # Header row — sender [room] · relative
        head_row = Text(INDENT)
        head_row.append("  ")
        head_row.append(f"@{sender}", style="bold primary")
        if show_room and room:
            head_row.append("  ", style="")
            head_row.append(f"#{room}", style="muted")
        if relative:
            head_row.append("  ·  ", style="dim")
            head_row.append(relative, style="muted")
        rows.append(head_row)

        # Body row — indented quote of the preview
        body_row = Text(INDENT)
        body_row.append("    ↳ ", style="dim")
        body_row.append(preview, style="bright")
        rows.append(body_row)

    if len(messages) > MAX_VISIBLE:
        tail = Text(INDENT)
        more = len(messages) - MAX_VISIBLE
        tail.append(f"  +{more} older — scroll to view", style="dim italic")
        rows.append(tail)

    return rows


def panel_summary(messages: Sequence[dict[str, Any]]) -> str:
    """One-line summary for the status bar / OS-level toast."""
    n = len(messages)
    if n == 0:
        return ""
    label = "DM" if n == 1 else "DMs"
    return f"{n} unread agent {label}"


__all__ = [
    "MAX_VISIBLE",
    "PREVIEW_CHARS",
    "panel_summary",
    "render_dm_inbox_panel",
]
