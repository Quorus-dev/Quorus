"""WhatsApp / iMessage-style chat surface for the Quorus TUI.

Pure-render module. No I/O, no global state, no Rich side-effects beyond
returning ``Text`` / ``ConsoleRenderable`` instances. The hub composes
these into the live frame.

Public surface
--------------
* :func:`render_app_bar`        — pinned top app-bar for in-room view
* :func:`render_bubble_feed`    — message bubbles (own / other / system)
* :func:`render_share_card`     — polished share card returned from `s`
* :func:`render_mention_popover` — `@…` autocomplete strip above composer
* :func:`copy_to_clipboard`     — best-effort cross-platform copy
* :func:`render_composer_hint`  — single dim placeholder under the feed

Conventions
~~~~~~~~~~~
* Two-space left margin everywhere (matches ``render.INDENT``).
* Theme tokens only — never literal hex.
* No emojis. ASCII-only glyphs everywhere on the visible surface.
* Continuation grouping window: ``GROUP_GAP_S`` seconds.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Iterable

from rich.text import Text

from .render import INDENT, two_col

# ── Tunables ─────────────────────────────────────────────────────────────────

GROUP_GAP_S = 120          # within this gap, same-sender msgs collapse
MAX_FEED_WIDTH_PCT = 0.70  # bubble body wraps at this fraction of the term
SHARE_CARD_MIN_W = 52      # share card never narrower than this

# System-event message types (centered, italic, dim — like iMessage hints).
_SYSTEM_TYPES = frozenset({"join", "leave", "lock", "decision", "system"})

# Sender palette — mirrors hub._SENDER_COLORS so the avatar dot picks the
# same color the sender's name renders in. Kept local to avoid an import
# cycle (chat.py imports nothing from hub.py).
_SENDER_PALETTE = ("primary", "room", "accent")


def _sender_color(name: str) -> str:
    """Deterministic theme-token color for *name*. Mirrors hub._sender_color."""
    import hashlib

    digest = hashlib.blake2s(name.encode("utf-8"), digest_size=2).digest()
    return _SENDER_PALETTE[int.from_bytes(digest, "big") % len(_SENDER_PALETTE)]


def _ts_epoch(ts_raw: str) -> float:
    """ISO-8601 → epoch seconds. 0 on parse failure."""
    if not ts_raw:
        return 0.0
    try:
        normalized = ts_raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _hhmm(ts_raw: str) -> str:
    """Extract HH:MM from an ISO timestamp. Empty on failure."""
    try:
        return ts_raw[11:16] if len(ts_raw) >= 16 else ""
    except Exception:
        return ""


def _wrap_lines(content: str, width: int) -> list[str]:
    """Word-wrap *content* into lines no wider than *width* cells.

    Preserves user-supplied newlines. ``width`` is clamped to a sane
    floor so long-token paragraphs never explode. Plain-text only — code
    fences are detected by the caller and rendered separately.
    """
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


# ── Top app-bar ──────────────────────────────────────────────────────────────


def render_app_bar(
    *,
    room_name: str,
    member_count: int,
    last_active: str,
    console_width: int,
) -> Text:
    """Pinned single-row in-room app-bar.

    Layout:
        ``  <-back   #room   · N members · last-active     ⋯ (s)hare (i)nfo (m)embers (l)eave``
    The ``<-`` arrow is a hint that ``Esc`` returns to welcome.
    """
    left = Text(INDENT)
    left.append("<-", style="dim")
    left.append("  ", style="dim")
    left.append(f"#{room_name}", style="bold room")
    if member_count:
        left.append("  ·  ", style="dim")
        suffix = "member" if member_count == 1 else "members"
        left.append(f"{member_count} {suffix}", style="muted")
    if last_active:
        left.append("  ·  ", style="dim")
        left.append(last_active, style="muted")

    right = Text()
    right.append("⋯  ", style="dim")
    for ch, label in (("s", "hare"), ("i", "nfo"), ("m", "embers"), ("l", "eave")):
        right.append("(", style="dim")
        right.append(ch, style="kbd")
        right.append(")", style="dim")
        right.append(label, style="muted")
        right.append("  ", style="")
    return two_col(left, right, total_width=max(40, console_width))


# ── Bubble feed ──────────────────────────────────────────────────────────────


def _bubble_width(console_width: int) -> int:
    """Inner text width of a bubble body line."""
    return max(20, int((console_width - 4) * MAX_FEED_WIDTH_PCT))


def _own_bubble(content: str, hhmm: str, console_width: int, *, receipt: str) -> list[Text]:
    """Right-aligned own-message bubble.

    No sender label (it's you). Subtle accent color for the body, tiny
    timestamp + delivery receipt at the right edge.
    """
    inner = _bubble_width(console_width)
    body = _wrap_lines(content, inner)
    out: list[Text] = []
    for i, line in enumerate(body):
        is_last = i == len(body) - 1
        # Body: right-aligned within the available width.
        suffix = (len(hhmm) + len(receipt) + 2) if is_last else 0
        pad = max(2, console_width - 2 - len(line) - suffix)
        row = Text(" " * pad)
        row.append(line, style="bright")
        if is_last and (hhmm or receipt):
            row.append("  ")
            if hhmm:
                row.append(hhmm, style="ts")
            if receipt:
                row.append(" ")
                row.append(receipt, style="success" if receipt.strip() == "✓✓" else "muted")
        out.append(row)
    return out


def _other_bubble(
    content: str,
    sender: str,
    hhmm: str,
    console_width: int,
    *,
    show_header: bool,
) -> list[Text]:
    """Left-aligned other-person bubble with avatar dot + sender header."""
    inner = _bubble_width(console_width)
    body = _wrap_lines(content, inner)
    color = _sender_color(sender)
    out: list[Text] = []

    if show_header:
        head = Text(INDENT)
        head.append("●", style=color)
        head.append("  ")
        head.append(f"@{sender}", style=f"bold {color}")
        out.append(head)

    # Body lines indent past the avatar so the "block" reads as the sender's.
    body_indent = "    "
    for i, line in enumerate(body):
        is_last = i == len(body) - 1
        row = Text(body_indent)
        row.append(line, style="bright")
        if is_last and hhmm:
            # Right-edge timestamp on the final line of the bubble.
            used = len(body_indent) + len(line)
            pad = max(2, console_width - 2 - used - len(hhmm))
            row.append(" " * pad)
            row.append(hhmm, style="ts")
        out.append(row)
    return out


def _system_event(content: str, sender: str, console_width: int) -> Text:
    """Centered, italic, dim — iMessage's "Arav joined" style."""
    text = content if sender in content else f"{sender} {content}".strip()
    pad = max(2, (console_width - len(text)) // 2)
    line = Text(" " * pad)
    line.append(text, style="dim italic")
    return line


def _unread_divider(console_width: int) -> Text:
    """Centered dotted rule with a 'new' label — only when unread > 0."""
    label = "  new  "
    side = max(8, (console_width - len(label) - 4) // 2)
    bar = "·  " * (side // 3) or "·  "
    line = Text("  ")
    line.append(bar.rstrip(), style="dim")
    line.append(label, style="bold muted")
    line.append(bar.rstrip(), style="dim")
    return line


def _code_block(body: str, console_width: int) -> list[Text]:
    """Render a triple-backtick block with subtle background tint.

    Uses spaces + dim styling rather than Panel so it composes inside
    the bubble feed without breaking the two-space margin rhythm.
    """
    inner = _bubble_width(console_width)
    out: list[Text] = []
    for raw in body.splitlines() or [""]:
        for chunk in [raw[i:i + inner] for i in range(0, max(1, len(raw)), inner)] or [""]:
            row = Text("    │ ", style="dim")
            row.append(chunk, style="bright")
            out.append(row)
    return out


def _split_code_fences(content: str) -> list[tuple[str, str]]:
    """Split *content* into [(kind, body), …] where kind is 'text' or 'code'."""
    parts: list[tuple[str, str]] = []
    buf: list[str] = []
    in_code = False
    for line in content.splitlines():
        if line.strip().startswith("```"):
            if buf:
                parts.append(("code" if in_code else "text", "\n".join(buf)))
                buf = []
            in_code = not in_code
            continue
        buf.append(line)
    if buf:
        parts.append(("code" if in_code else "text", "\n".join(buf)))
    return parts or [("text", content)]


def _delivery_receipt(msg: dict) -> str:
    """One/two-checkmark receipt for own messages.

    * `· ` queued (no id yet)
    * `✓ ` sent (we have an id)
    * `✓✓` delivered + read (presence-based; placeholder for future read receipts)
    """
    if msg.get("read_by"):  # forward-looking
        return "✓✓"
    if msg.get("id") or msg.get("message_id"):
        return "✓"
    return "·"


def render_bubble_feed(
    messages: list[dict],
    room_name: str,
    my_name: str,
    *,
    console_width: int = 80,
    first_unread_index: int | None = None,
) -> list[Text]:
    """Render *messages* as a bubble feed.

    * Group consecutive same-sender messages within ``GROUP_GAP_S``
      seconds — only the first shows the sender header.
    * System events (join/leave/lock/decision) render centered + dim.
    * Own messages right-align with a delivery receipt; other people's
      left-align with a colored avatar dot.
    * If *first_unread_index* is provided and points inside *messages*,
      a centered "new" divider renders just above that index.
    """
    if not room_name:
        return _empty_card(
            console_width,
            "No room selected",
            "Press Tab to cycle, or run /new <name>",
        )
    if not messages:
        return _empty_card(
            console_width,
            f"#{room_name} is quiet",
            "No messages yet — say hi",
        )

    out: list[Text] = []
    prev_sender: str | None = None
    prev_ts: float = 0.0
    prev_was_system = False

    for i, msg in enumerate(messages):
        if first_unread_index is not None and i == first_unread_index and i > 0:
            out.append(Text(""))
            out.append(_unread_divider(console_width))
            out.append(Text(""))
            # Force a fresh header on the next bubble.
            prev_sender = None

        sender = msg.get("from_name") or msg.get("sender") or "?"
        content = str(msg.get("content", ""))
        mtype = msg.get("message_type", "chat")
        ts_raw = msg.get("timestamp", "")
        ts = _ts_epoch(ts_raw)
        hhmm = _hhmm(ts_raw)

        # System event? Centered + dim.
        if mtype in _SYSTEM_TYPES:
            if out and not prev_was_system:
                out.append(Text(""))
            out.append(_system_event(content, sender, console_width))
            prev_was_system = True
            prev_sender = None
            prev_ts = ts
            continue

        is_me = sender == my_name
        same_group = (
            sender == prev_sender
            and prev_ts
            and (ts - prev_ts) < GROUP_GAP_S
        )

        if not same_group and out:
            out.append(Text(""))

        # Hide HHMM on continuation lines unless we crossed a 5-min boundary,
        # so the bubble feed doesn't feel like a log file.
        show_ts = (not same_group) or ((ts - prev_ts) > 300)
        ts_show = hhmm if show_ts else ""

        # Code-fence aware rendering — preserve the bubble for text spans
        # and switch to the code renderer for fenced spans.
        for kind, body in _split_code_fences(content):
            if not body and kind == "text":
                continue
            if kind == "code":
                out.extend(_code_block(body, console_width))
                continue
            if is_me:
                receipt = _delivery_receipt(msg) if show_ts else ""
                out.extend(_own_bubble(body, ts_show, console_width, receipt=receipt))
            else:
                out.extend(
                    _other_bubble(
                        body,
                        sender,
                        ts_show,
                        console_width,
                        show_header=not same_group,
                    )
                )

        prev_sender = sender
        prev_ts = ts
        prev_was_system = False

    return out


def _empty_card(console_width: int, title: str, cta: str) -> list[Text]:
    """Centered 5-line empty state. Mirrors hub._empty_card visually."""

    def _center(s: str, style: str) -> Text:
        pad = max(0, (console_width - len(s)) // 2)
        return Text(" " * pad + s, style=style)

    return [
        Text(""),
        _center("◌", "bold primary"),
        Text(""),
        _center(title, "muted"),
        Text(""),
        _center(cta, "dim"),
    ]


# ── Composer hint ────────────────────────────────────────────────────────────


def render_composer_hint() -> Text:
    """Single dim placeholder rendered just above the prompt."""
    line = Text(INDENT)
    line.append("│ ", style="primary")
    line.append("Type a message…  ", style="dim")
    line.append("@", style="kbd")
    line.append(" mention  ", style="dim")
    line.append("/", style="kbd")
    line.append(" command", style="dim")
    return line


# ── Share card ───────────────────────────────────────────────────────────────


def _short_code(code: str) -> str:
    """Display-friendly short-code.

    Server-issued codes are already short (e.g. ``MJN2-EWVT``). The legacy
    base64 envelope is long — truncate the middle so it still fits the
    card. We never modify the actual code returned to the user — only the
    DISPLAY string. Callers always copy the original.
    """
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
        # Two-space pad inside the bordered card.
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
    lines.append(_row(f"Expires: {ttl_label}  ·  press c to copy, any key to close", style="dim"))
    lines.append(bottom)
    return lines


# ── @-mention popover ────────────────────────────────────────────────────────


def render_mention_popover(query: str, members: Iterable[str], *, max_rows: int = 5) -> list[Text]:
    """Up to *max_rows* matching members rendered as a small popover.

    The query matches case-insensitively against member-name prefix first,
    then falls back to a substring match. Empty list when nothing matches.
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
        line.append("●", style=_sender_color(name))
        line.append("  ")
        line.append(f"@{name}", style=f"bold {_sender_color(name)}")
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
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=2)
            return True
        if sys_name == "Linux":
            for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=2)
                    return True
            # Wayland fallback.
            if shutil.which("wl-copy"):
                subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True, timeout=2)
                return True
            return False
        if sys_name == "Windows" and shutil.which("clip"):
            # `clip` reads from stdin on Windows; encode utf-16-le for safety.
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True, timeout=2)
            return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return False


# ── Last-active formatter (used by the app-bar) ──────────────────────────────


def last_active_label(messages: list[dict], *, now: datetime | None = None) -> str:
    """Return a short "active 2m ago" / "active just now" label.

    Empty when there are no messages with timestamps.
    """
    now = now or datetime.now(timezone.utc)
    for msg in reversed(messages):
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


# Re-export so callers can introspect without poking at private helpers.
__all__ = [
    "GROUP_GAP_S",
    "copy_to_clipboard",
    "last_active_label",
    "render_app_bar",
    "render_bubble_feed",
    "render_composer_hint",
    "render_mention_popover",
    "render_share_card",
]


# Defensive import: ensure os.path is importable in environments where the
# clipboard probes run before standard subprocess wiring (no-op normally).
_ = os
