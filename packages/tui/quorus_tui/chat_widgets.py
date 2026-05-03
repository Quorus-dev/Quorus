"""Low-level chat widgets — the visual atoms behind ``chat.py``.

Split out so ``chat.py`` (the orchestrator) stays small and the polish-pass
widgets can be unit-tested in isolation. Pure-render: every public function
returns a Rich ``Text`` (or list of) with no I/O — except :func:`copy_to_clipboard`,
which is the one acknowledged side-effect (and is opt-in, only invoked when
the user hits ``c`` on a share card).

What lives here
---------------
Identity
* :func:`sender_color`         — hash a name → one of 6 sender theme tokens
* :func:`is_human_sender`      — `arav-codex` → False, `arav-pm` → True

Layout primitives
* :func:`bubble_width`         — inner text width given the terminal
* :func:`wrap_lines`           — word-wrap with newline preservation
* :func:`bubble_corners`       — corner glyphs for grouped/standalone bubbles

Time
* :func:`hhmm`                 — ISO-8601 → "14:42"
* :func:`ts_epoch`             — ISO-8601 → epoch seconds
* :func:`time_divider`         — centered "Today, 2:42 PM" rule
* :func:`time_divider_label`   — format-only helper for the rule above
* :func:`last_active_label`    — short relative-time label for the app-bar

Surface fragments
* :func:`highlight_inline`     — colorize ``@user`` / ``#room`` spans inline
* :func:`reaction_row`         — tiny pill chips below long-form messages
* :func:`read_receipt_row`     — right-aligned dim "Delivered/Read" micro-row
* :func:`empty_room_card`      — iMessage-y centered ASCII illustration
* :func:`typing_indicator`     — pulsing "@x is typing…" hint
* :func:`presence_dot`         — green ● if room had recent activity, else dim
* :func:`render_share_card`    — polished share card (used by /share)
* :func:`render_mention_popover` — `@…` autocomplete strip

Side-effects (clearly isolated)
* :func:`copy_to_clipboard`    — best-effort cross-platform copy

Design constraints
~~~~~~~~~~~~~~~~~~
* Theme tokens only — no literal hex anywhere.
* ASCII-friendly glyphs (`╭ ╰ ╮ ╯ │`, `·`, `—`) — no emoji on the surface.
* Two-space left margin via ``INDENT`` from :mod:`.render`.
"""

from __future__ import annotations

import hashlib
import platform
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Iterable

from rich.text import Text

from .render import INDENT

# ── Sender palette ───────────────────────────────────────────────────────────
# 6 distinct semantic theme tokens — see ``quorus_cli.ui.THEME``. Bumped from
# the previous 3 so a 10+ member room has visibly distinct name colors.
SENDER_PALETTE: tuple[str, ...] = (
    "sender1", "sender2", "sender3", "sender4", "sender5", "sender6",
)

# Heuristic agent-name keywords — matches hub._name_has_agent_keyword.
_AGENT_KEYWORDS = ("codex", "claude", "gemini", "cursor", "bot")

# Inline pattern: `@user` or `#room`. Stop at whitespace, comma, period
# (to avoid swallowing trailing punctuation), and a curated set of CJK /
# punctuation neighbours. Lowercase letters, digits, dashes, dots and
# underscores are valid identifier chars.
_INLINE_PATTERN = re.compile(r"(?<![A-Za-z0-9_])([@#][A-Za-z0-9_][A-Za-z0-9_.\-]*)")

# How many seconds without messages before a time-divider kicks in.
TIME_DIVIDER_GAP_S = 300  # 5 minutes — matches iMessage chat-session boundary


# ── Sender identity ──────────────────────────────────────────────────────────


def sender_color(name: str) -> str:
    """Deterministic theme-token color for *name*.

    Hashed with blake2s so the bucket survives Python restarts (the
    built-in ``hash()`` is PYTHONHASHSEED-randomized). Returning a theme
    token (not a hex) keeps callers free of literal styles.
    """
    digest = hashlib.blake2s(name.encode("utf-8"), digest_size=2).digest()
    return SENDER_PALETTE[int.from_bytes(digest, "big") % len(SENDER_PALETTE)]


def is_human_sender(name: str) -> bool:
    """True iff *name* doesn't carry a known agent-harness keyword.

    Same heuristic as ``hub._name_has_agent_keyword`` — duplicated here
    intentionally to avoid a chat→hub import cycle. Plain names without
    a dash are always treated as human (e.g. ``arav``).
    """
    if not name or "-" not in name:
        return True
    parts = name.lower().split("-")[1:]
    return not any(kw in p for p in parts for kw in _AGENT_KEYWORDS)


# ── Layout primitives ────────────────────────────────────────────────────────


MAX_FEED_WIDTH_PCT = 0.70


def bubble_width(console_width: int) -> int:
    """Inner text width of a bubble body line given the terminal width."""
    return max(20, int((console_width - 4) * MAX_FEED_WIDTH_PCT))


def wrap_lines(content: str, width: int) -> list[str]:
    """Word-wrap *content* to *width* cells. Preserves explicit newlines."""
    width = max(20, width)
    out: list[str] = []
    for paragraph in content.splitlines() or [""]:
        if not paragraph:
            out.append("")
            continue
        words = paragraph.split(" ")
        line = ""
        for word in words:
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                out.append(line)
                line = word
        if line:
            out.append(line)
    return out


# ── Time helpers ─────────────────────────────────────────────────────────────


def hhmm(ts_raw: str) -> str:
    """Extract HH:MM from an ISO-8601 timestamp; empty on parse failure."""
    try:
        return ts_raw[11:16] if len(ts_raw) >= 16 else ""
    except Exception:
        return ""


def ts_epoch(ts_raw: str) -> float:
    """ISO-8601 → epoch seconds; ``0.0`` on parse failure."""
    if not ts_raw:
        return 0.0
    try:
        normalized = ts_raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _local_dt(ts_raw: str) -> datetime | None:
    """ISO-8601 → tz-aware ``datetime`` in local time; None on failure."""
    if not ts_raw:
        return None
    try:
        normalized = ts_raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except (ValueError, TypeError):
        return None


def time_divider_label(
    ts_raw: str,
    *,
    now: datetime | None = None,
) -> str:
    """Format a divider label like ``"Today, 2:42 PM"`` / ``"Mar 14, 9:01 AM"``.

    Returns an empty string when *ts_raw* can't be parsed. Tomorrow / future
    timestamps render as bare dates so we never gaslight the user.
    """
    dt = _local_dt(ts_raw)
    if dt is None:
        return ""
    now = (now or datetime.now(timezone.utc)).astimezone()
    today = now.date()
    yday = (now - _ONE_DAY).date()

    # 12-hour clock with AM/PM, lower-cased "am/pm" reads softer.
    time_part = dt.strftime("%-I:%M %p")
    if dt.date() == today:
        return f"Today, {time_part}"
    if dt.date() == yday:
        return f"Yesterday, {time_part}"
    if dt.year == now.year:
        return dt.strftime("%a %b %-d, ") + time_part
    return dt.strftime("%b %-d %Y, ") + time_part


_ONE_DAY = timedelta(days=1)


# ── Inline highlighters ──────────────────────────────────────────────────────


def highlight_inline(content: str, *, body_style: str = "bright") -> Text:
    """Return a Rich ``Text`` with ``@mentions`` and ``#rooms`` styled accent.

    Mentions and rooms render in **bold accent** so the eye catches them
    inside the bubble body. The rest of the text uses *body_style*. This
    is purely visual — no link/click semantics in a terminal.
    """
    out = Text()
    pos = 0
    for match in _INLINE_PATTERN.finditer(content):
        if match.start() > pos:
            out.append(content[pos:match.start()], style=body_style)
        token = match.group(1)
        out.append(token, style="bold accent")
        pos = match.end()
    if pos < len(content):
        out.append(content[pos:], style=body_style)
    return out


# ── Time divider ─────────────────────────────────────────────────────────────


def time_divider(label: str, console_width: int) -> Text:
    """Centered dim ``— Today, 2:42 PM —`` divider rule.

    Returned as a Rich ``Text`` — Rich's ``Rule`` would force its own
    spacing and break the two-space left-margin convention.
    """
    if not label:
        return Text("")
    side = max(4, (console_width - len(label) - 4) // 2)
    bar = "─" * side
    line = Text("  ")
    line.append(bar, style="dim")
    line.append("  ")
    line.append(label, style="dim italic")
    line.append("  ")
    line.append(bar, style="dim")
    return line


# ── Read receipt + reactions ─────────────────────────────────────────────────


def read_receipt_row(receipt: str, console_width: int) -> Text:
    """Right-aligned dim micro-row beneath the user's most recent own bubble.

    The receipt string is one of:
      * ``"Delivered"`` — message arrived at the relay (we have an id)
      * ``"Read"``      — at least one peer has acked (presence-based, future)
      * ``"Sending…"``  — outbound, no id yet
    Empty input returns a blank line so the caller can unconditionally
    print and let layout collapse — but typically the caller short-circuits.
    """
    if not receipt:
        return Text("")
    pad = max(2, console_width - 2 - len(receipt))
    line = Text(" " * pad)
    line.append(receipt, style="dim italic")
    return line


def receipt_label(msg: dict) -> str:
    """Choose a receipt label given a message dict.

    Returns one of: ``"Sending…"`` (no id yet), ``"Delivered"`` (has id),
    ``"Read"`` (peer ack present). Never raises — falls through to empty
    on a malformed dict.
    """
    if not isinstance(msg, dict):
        return ""
    if msg.get("read_by"):
        return "Read"
    if msg.get("id") or msg.get("message_id"):
        return "Delivered"
    return "Sending…"


# ── Social Protocol v1 — verb decoration ─────────────────────────────────────
# Zero-width marker that chat.render_bubble_feed reads to know it should swap
# the bubble's border style to ``danger`` for the immediately-following bubble.
# Marker text is NOT rendered to the user; the surrounding zero-width spaces
# are deliberate so re-rendering the line in unrelated tests doesn't hit a
# cosmetic snag.
_INTERRUPT_BORDER_MARKER = "​__quorus_interrupt_border__​"


def verb_decoration(
    verb: str,
    payload: dict,
    console_width: int,
    *,
    sender: str = "",
) -> list[Text]:
    """Render the decoration row(s) for a Quorus social verb.

    Returns 1 :class:`Text` for most verbs; 2 for ``interrupt`` (the second
    row carries :data:`_INTERRUPT_BORDER_MARKER` which signals
    ``chat.render_bubble_feed`` to swap the bubble corner style to
    ``danger``). Theme tokens only — no literal hex, no emoji.

    Per the Stream A render contract::

        claim     → "▶ claimed <task> · ETA <m>m"     sender_color
        release   → "■ released <task> → @x"          dim
        disagree  → "⚠ disagree (blocking|advisory)"  danger | warning
        defer     → "↪ deferring to @x"               dim
        queue     → "≡ queued after #<after>"         info
        vote      → "✓ vote: <option> (<weight>)"     success
        interrupt → "! INTERRUPT — <reason>"          danger + border marker

    *console_width* is accepted for symmetry with siblings (e.g.
    :func:`reaction_row`); the decoration itself is a single-line caption,
    not a wrapped block.
    """
    del console_width  # currently single-line; reserved for future wrapping
    if not verb or not isinstance(payload, dict):
        return []
    color = sender_color(sender) if sender else "muted"
    line = Text(INDENT)

    if verb == "claim":
        task = str(payload.get("task_id", "?"))[:60]
        eta = int(payload.get("eta_seconds", 0))
        eta_label = f"{eta // 60}m" if eta >= 60 else f"{eta}s"
        line.append("▶ ", style=color)
        line.append(f"claimed {task}", style="bright")
        line.append(f"  ·  ETA {eta_label}", style="muted")
    elif verb == "release":
        task = str(payload.get("task_id", "?"))[:60]
        handoff = payload.get("handoff_to")
        line.append("■ ", style="dim")
        line.append(f"released {task}", style="bright")
        if handoff:
            line.append(f" → @{handoff}", style="muted")
    elif verb == "disagree":
        mode = str(payload.get("mode", "advisory"))
        style = "danger" if mode == "blocking" else "warning"
        line.append("⚠ ", style=style)
        line.append(f"disagree ({mode})", style=f"bold {style}")
        reason = str(payload.get("reason", ""))[:80]
        if reason:
            line.append(f" — {reason}", style="muted")
    elif verb == "defer":
        target = str(payload.get("to", "?"))
        line.append("↪ ", style="dim")
        line.append(f"deferring to @{target}", style="muted")
    elif verb == "queue":
        after = str(payload.get("after", "?"))[:40]
        summary = str(payload.get("task_summary", ""))[:60]
        line.append("≡ ", style="info")
        line.append(f"queued after #{after}", style="info")
        if summary:
            line.append(f" — {summary}", style="muted")
    elif verb == "vote":
        option = str(payload.get("option", "?"))[:40]
        weight = float(payload.get("weight", 1.0))
        line.append("✓ ", style="success")
        line.append(f"vote: {option}", style="bold success")
        line.append(f"  ({weight:g})", style="muted")
    elif verb == "interrupt":
        reason = str(payload.get("reason", ""))[:120]
        line.append("! INTERRUPT", style="bold danger")
        if reason:
            line.append(f" — {reason}", style="danger")
        marker = Text(_INTERRUPT_BORDER_MARKER, style="danger")
        return [line, marker]
    else:
        return []
    return [line]


def reaction_row(reactions: dict[str, int], console_width: int) -> Text:
    """Render a row of ``[heart 2] [thumbs-up 1]`` chip-pills.

    *reactions* maps a label → count. Label is the ASCII name (e.g.
    ``"heart"``, ``"thumbs-up"``, ``"sparkle"``); we never embed Unicode
    emoji on the rendered surface (project rule). Empty dict → blank Text.
    """
    if not reactions:
        return Text("")
    line = Text(INDENT * 2)  # body-indent so chips align under the message
    first = True
    for label, count in reactions.items():
        if not first:
            line.append("  ")
        first = False
        line.append("[", style="dim")
        line.append(label, style="muted")
        if count and count > 1:
            line.append(f" {count}", style="bold accent")
        line.append("]", style="dim")
    # Truncate at console_width if it overflows (rare but safe).
    if line.cell_len > console_width:
        return Text("")
    return line


# ── Empty-room card ──────────────────────────────────────────────────────────


def empty_room_card(room_name: str, console_width: int) -> list[Text]:
    """iMessage-y centered illustration for a room with zero messages.

    Renders:
      ``               #room                  `` (big, primary)
      ``     say hi to start the conversation `` (muted)
      ``                  ▌                    `` (blinking cursor — bright)
    The "blink" is purely visual — a bright vertical bar that the human
    brain reads as a pulse. Static across renders to avoid Live thrashing.
    """
    title = f"#{room_name}"
    cta = "say hi to start the conversation"
    cursor = "▌"

    def _center(s: str, style: str) -> Text:
        pad = max(0, (console_width - len(s)) // 2)
        return Text(" " * pad + s, style=style)

    return [
        Text(""),
        Text(""),
        _center(title, "bold room"),
        Text(""),
        _center(cta, "muted italic"),
        Text(""),
        _center(cursor, "bold accent"),
    ]


# ── Typing indicator ─────────────────────────────────────────────────────────


def typing_indicator(typist: str | None) -> Text:
    """``@x is typing…`` hint. Empty Text when no one's typing.

    The ellipsis pulses visually via dim-italic styling; we don't
    re-render to animate (would thrash the Live region) — the human
    eye reads italics + ellipsis as motion regardless.
    """
    if not typist:
        return Text("")
    line = Text(INDENT)
    line.append("●  ", style=sender_color(typist))
    line.append(f"@{typist}", style="muted")
    line.append(" is typing", style="dim italic")
    line.append("…", style="dim italic")
    return line


# ── Bubble corner glyphs ─────────────────────────────────────────────────────


def bubble_corners(*, position: str, side: str = "left") -> tuple[str, str]:
    """Return (top, bottom) corner glyphs for a bubble line.

    *position* is one of:
      * ``"only"``  — single message (rounded both top + bottom)
      * ``"first"`` — first in a sender-group (rounded top, continuation bot)
      * ``"mid"``   — interior of a sender-group (continuation both sides)
      * ``"last"``  — last in a sender-group (continuation top, rounded bot)

    *side* is ``"left"`` (other-bubble) or ``"right"`` (own-bubble) — we
    flip the corner direction to match. The rendered corners are
    decorative — placed in the avatar/timestamp gutter.
    """
    if side == "right":
        top_round, bot_round, cont = "╮", "╯", "│"
    else:
        top_round, bot_round, cont = "╭", "╰", "│"
    if position == "only":
        return (top_round, bot_round)
    if position == "first":
        return (top_round, cont)
    if position == "last":
        return (cont, bot_round)
    return (cont, cont)


# ── Presence dot ─────────────────────────────────────────────────────────────


def presence_dot(messages: list[dict], *, now: datetime | None = None,
                 active_window_s: int = 60) -> tuple[str, str]:
    """Return ``(glyph, style)`` for the room-presence indicator.

    Active = anyone (other than system) posted within *active_window_s*
    seconds. Active rooms get a green ●; idle rooms get a dim ○.
    """
    now = now or datetime.now(timezone.utc)
    for msg in reversed(messages or []):
        if msg.get("message_type") == "system":
            continue
        ts_raw = msg.get("timestamp", "")
        if not ts_raw:
            continue
        ts = ts_epoch(ts_raw)
        if ts <= 0:
            continue
        if now.timestamp() - ts <= active_window_s:
            return ("●", "success")
        break
    return ("○", "dim")


# ── Sender-color collision audit ─────────────────────────────────────────────


def color_collision_rate(names: Iterable[str]) -> float:
    """Return the pairwise collision probability for the palette + *names*.

    Computed as 1 - (distinct_buckets / palette_size). With 20 names on
    a 6-color palette and a good hash, *distinct_buckets* should equal
    the palette size (≥ palette_size names guarantees full coverage if
    the hash is unbiased). We use this in a regression test to catch
    palette regressions (e.g. someone trims the tuple back to 3 — which
    would push distinct_buckets to 3 and the metric to 0.5).
    """
    names = list(names)
    if not names:
        return 0.0
    buckets = {sender_color(n) for n in names}
    palette_size = len(SENDER_PALETTE)
    return 1.0 - (len(buckets) / palette_size)


# ── Share card ───────────────────────────────────────────────────────────────

SHARE_CARD_MIN_W = 52  # share card never narrower than this


def _short_code(code: str) -> str:
    """Display-friendly short-code (DISPLAY only — never alters copy buffer)."""
    if len(code) <= 32:
        return code
    return code[:14] + "…" + code[-6:]


def render_share_card(
    *,
    room_name: str,
    code: str,
    install_url: str | None = None,
    console_width: int = 80,
    ttl_label: str = "7 days",
) -> list[Text]:
    """A polished share card for the room. Returns Text lines, no panel."""
    width = max(SHARE_CARD_MIN_W, min(console_width - 4, 64))
    title = f" Share #{room_name} "
    side = max(2, (width - len(title) - 2) // 2)
    top = Text("  ┌" + "─" * side, style="primary")
    top.append(title, style="bold primary")
    top.append("─" * (width - 2 - side - len(title)) + "┐", style="primary")

    def _row(left: str, *, style: str = "bright") -> Text:
        body = "  " + left
        pad = max(0, width - 2 - len(body))
        line = Text("  │", style="primary")
        line.append(body, style=style)
        line.append(" " * pad, style="")
        line.append("│", style="primary")
        return line

    def _blank() -> Text:
        line = Text("  │", style="primary")
        line.append(" " * (width - 2), style="")
        line.append("│", style="primary")
        return line

    bottom = Text("  └" + "─" * (width - 2) + "┘", style="primary")

    lines: list[Text] = [top, _blank()]
    lines.append(_row(f"Code:   {_short_code(code)}", style="bold accent"))
    lines.append(_blank())
    if install_url:
        lines.append(_row("One-line install:", style="muted"))
        lines.append(_row(install_url, style="bright"))
        lines.append(_blank())
    lines.append(_row(f"Or:  quorus join {_short_code(code)}", style="muted"))
    lines.append(_blank())
    lines.append(_row(
        f"Expires: {ttl_label}  ·  press c to copy, any key to close",
        style="dim",
    ))
    lines.append(bottom)
    return lines


# ── @-mention popover ────────────────────────────────────────────────────────


def render_mention_popover(
    query: str, members: Iterable[str], *, max_rows: int = 5,
) -> list[Text]:
    """Up to *max_rows* matching members rendered as a small popover.

    Prefix matches lead, substring matches follow. Empty list when nothing
    matches — caller should suppress the popover entirely in that case.
    """
    q = (query or "").lower().lstrip("@")
    members = [m for m in members if m]
    if not members:
        return []

    prefix = [m for m in members if m.lower().startswith(q)]
    seen = set(prefix)
    other = [m for m in members if q in m.lower() and m not in seen]
    matches = (prefix + other)[:max_rows]
    if not matches:
        return []

    rows: list[Text] = []
    head = Text(INDENT)
    head.append("@", style="kbd")
    head.append("  Mention  ", style="dim")
    head.append("Tab", style="kbd")
    head.append(" to insert", style="dim")
    rows.append(head)
    for name in matches:
        line = Text("  ")
        line.append("●", style=sender_color(name))
        line.append("  ")
        line.append(f"@{name}", style=f"bold {sender_color(name)}")
        rows.append(line)
    return rows


# ── Clipboard ────────────────────────────────────────────────────────────────


def copy_to_clipboard(text: str) -> bool:
    """Best-effort cross-platform clipboard copy. Returns True on success.

    Prefers native tools (pbcopy/xclip/clip) over Python deps so this stays
    zero-dependency. Silently fails on headless Linux without xclip — the
    caller surfaces a "couldn't copy" status instead of crashing.
    """
    sys_name = platform.system()
    try:
        if sys_name == "Darwin" and shutil.which("pbcopy"):
            subprocess.run(
                ["pbcopy"], input=text.encode("utf-8"),
                check=True, timeout=2,
            )
            return True
        if sys_name == "Linux":
            for cmd in (
                ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
            ):
                if shutil.which(cmd[0]):
                    subprocess.run(
                        cmd, input=text.encode("utf-8"),
                        check=True, timeout=2,
                    )
                    return True
            if shutil.which("wl-copy"):
                subprocess.run(
                    ["wl-copy"], input=text.encode("utf-8"),
                    check=True, timeout=2,
                )
                return True
            return False
        if sys_name == "Windows" and shutil.which("clip"):
            subprocess.run(
                ["clip"], input=text.encode("utf-16-le"),
                check=True, timeout=2,
            )
            return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return False


# ── Last-active formatter (used by the app-bar) ──────────────────────────────


def last_active_label(
    messages: list[dict], *, now: datetime | None = None,
) -> str:
    """Return a short "active 2m ago" / "active just now" label.

    Empty when there are no messages with timestamps — the caller treats
    that as "no signal" and renders nothing.
    """
    now = now or datetime.now(timezone.utc)
    for msg in reversed(messages or []):
        ts_raw = msg.get("timestamp", "")
        if not ts_raw:
            continue
        try:
            normalized = ts_raw.replace("Z", "+00:00")
            ts = datetime.fromisoformat(normalized)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        secs = int((now - ts).total_seconds())
        if secs < 0:
            return ""
        if secs < 45:
            return "active just now"
        mins = secs // 60
        if mins < 60:
            return f"active {mins}m ago"
        hours = mins // 60
        if hours < 24:
            return f"active {hours}h ago"
        days = hours // 24
        return f"active {days}d ago"
    return ""


__all__ = [
    "INDENT",
    "MAX_FEED_WIDTH_PCT",
    "SENDER_PALETTE",
    "SHARE_CARD_MIN_W",
    "TIME_DIVIDER_GAP_S",
    "bubble_corners",
    "bubble_width",
    "color_collision_rate",
    "copy_to_clipboard",
    "empty_room_card",
    "hhmm",
    "highlight_inline",
    "is_human_sender",
    "last_active_label",
    "presence_dot",
    "reaction_row",
    "read_receipt_row",
    "receipt_label",
    "render_mention_popover",
    "render_share_card",
    "sender_color",
    "time_divider",
    "time_divider_label",
    "ts_epoch",
    "typing_indicator",
    "verb_decoration",
    "wrap_lines",
    "_INTERRUPT_BORDER_MARKER",
]
