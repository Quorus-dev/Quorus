"""Shared rendering helpers for the Quorus TUI.

Lives outside ``hub.py`` (which is already 2.5k lines) and outside
``welcome.py`` (which renders the home surface) so primitives stay
reusable. Pure functions only — no I/O, no global state, no Rich
side-effects beyond returning ``Text`` instances.

Conventions everything in this package follows:

  * Two-space left margin on every visible row.
  * Section spacing: exactly ONE blank line between sections, ZERO
    inside a section.
  * Unicode separator: ``·`` between inline tokens, ``—`` for
    section subheads, ``│`` for vertical pills.
  * Colors come from the theme tokens — no literal hex anywhere
    outside ``quorus_cli.ui``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from rich.text import Text

# ── Layout constants ─────────────────────────────────────────────────────────

INDENT = "  "          # every visible line begins here
SEP = "  ·  "          # inline separator between tokens
SUB_SEP = " · "        # tighter separator for header pills
DASH = " — "           # section subhead separator

# Column widths used by the welcome action menu so labels + hints align
# without ever having to measure terminal width.
ACTION_KEY_W = 5       # `[n]  `
ACTION_LABEL_W = 22    # left-aligned label column
ACTION_HINT_DIM = "muted"


# ── Time formatting ──────────────────────────────────────────────────────────


def relative_time(iso_ts: str, *, now: datetime | None = None) -> str:
    """Render a short relative timestamp ("2h ago", "just now", "3d ago").

    Returns an empty string if the timestamp can't be parsed — callers
    should treat that as "no signal" and render nothing.
    """
    if not iso_ts:
        return ""
    try:
        normalized = iso_ts.replace("Z", "+00:00")
        ts = datetime.fromisoformat(normalized)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""

    now = now or datetime.now(timezone.utc)
    delta = now - ts
    secs = int(delta.total_seconds())

    if secs < 0:
        # Clock skew — don't pretend we know.
        return ""
    if secs < 45:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


# ── Text composition ─────────────────────────────────────────────────────────


def join_inline(parts: Iterable[tuple[str, str]], *, sep_style: str = "dim") -> Text:
    """Join (text, style) parts with the standard ``·`` separator.

    Used everywhere a status-bar-ish line needs to be assembled.
    Pass empty strings for parts you want skipped — they're filtered
    so callers can build the list inline without conditional appends.
    """
    out = Text()
    first = True
    for body, style in parts:
        if not body:
            continue
        if not first:
            out.append(SEP, style=sep_style)
        out.append(body, style=style)
        first = False
    return out


def two_col(left: Text, right: Text, *, total_width: int) -> Text:
    """Compose a row with *left* flush-left and *right* flush-right.

    Falls back to a single-space separator if the two halves don't fit
    in *total_width* — never throws, never wraps.
    """
    pad = total_width - left.cell_len - right.cell_len
    if pad < 1:
        pad = 1
    out = Text()
    out.append_text(left)
    out.append(" " * pad)
    out.append_text(right)
    return out


def section_head(label: str, *, accent: str | None = None) -> Text:
    """A premium section subhead.

    Renders as ``  Label`` in bold primary plus, when *accent* is
    supplied, ``— accent`` in dim text (e.g. ``— 4 with new activity``).
    """
    head = Text(INDENT)
    head.append(label, style="bold primary")
    if accent:
        head.append(DASH, style="dim")
        head.append(accent, style="muted")
    return head


def quiet_rule_chars(width: int) -> str:
    """A flat dim hairline with consistent inset.

    Returns just the string — caller wraps with the right style.
    Width is clamped so it never overflows.
    """
    inner = max(20, width - 4)
    return "─" * inner
