"""WhatsApp / iMessage-style chat surface for the Quorus TUI.

Pure-render orchestrator over visual atoms in :mod:`.chat_widgets`. Returns
``Text`` / ``ConsoleRenderable`` only — no I/O, no global state. The hub
composes these into the live frame.

Defined here: :func:`render_app_bar`, :func:`render_bubble_feed`,
:func:`render_composer_hint`. Re-exported from chat_widgets for stable
imports: ``render_share_card``, ``render_mention_popover``,
``copy_to_clipboard``, ``last_active_label``.

Conventions: two-space left margin (``render.INDENT``); theme tokens only
(never literal hex); ASCII-only glyphs (no emoji); same-sender messages
within ``GROUP_GAP_S`` seconds collapse to zero spacing (iMessage rhythm).
"""

from __future__ import annotations

from rich.text import Text

from . import chat_widgets as _w
from .render import INDENT, two_col

# Pull every widget through a single namespace alias so the import block
# stays compact. The aliases below give chat.py the same callable surface
# as before the split — hub.py and slash.py keep importing from chat.
_p = _w  # type: ignore[assignment]
SENDER_PALETTE = _p.SENDER_PALETTE
SHARE_CARD_MIN_W = _p.SHARE_CARD_MIN_W
TIME_DIVIDER_GAP_S = _p.TIME_DIVIDER_GAP_S
MAX_FEED_WIDTH_PCT = _p.MAX_FEED_WIDTH_PCT
bubble_corners = _p.bubble_corners
bubble_width = _p.bubble_width
copy_to_clipboard = _p.copy_to_clipboard
empty_room_card = _p.empty_room_card
highlight_inline = _p.highlight_inline
is_human_sender = _p.is_human_sender
last_active_label = _p.last_active_label
presence_dot = _p.presence_dot
reaction_row = _p.reaction_row
read_receipt_row = _p.read_receipt_row
receipt_label = _p.receipt_label
render_mention_popover = _p.render_mention_popover
render_share_card = _p.render_share_card
sender_color = _p.sender_color
time_divider = _p.time_divider
time_divider_label = _p.time_divider_label
typing_indicator = _p.typing_indicator
verb_decoration = _p.verb_decoration
_INTERRUPT_BORDER_MARKER = _p._INTERRUPT_BORDER_MARKER
_hhmm, _ts_epoch, _wrap_lines = _p.hhmm, _p.ts_epoch, _p.wrap_lines
# Leading-underscore back-compat aliases used by older tests/hub.py.
_SENDER_PALETTE, _is_human_sender, _sender_color = (
    SENDER_PALETTE, is_human_sender, sender_color,
)

GROUP_GAP_S = 60  # tighter rhythm — iMessage groups within ~1 minute
_SYSTEM_TYPES = frozenset({"join", "leave", "lock", "decision", "system"})


def parse_verb(content: str):
    """Best-effort parse of a chat content body into a SocialVerb.

    Returns ``None`` if content is not a JSON envelope with ``kind=='social'``.
    Never raises — non-JSON content is the common case and must not break the
    feed renderer.
    """
    if not content or not content.startswith("{"):
        return None
    try:
        import json

        from quorus.protocol import parse_envelope
        data = json.loads(content)
        if not isinstance(data, dict) or data.get("kind") != "social":
            return None
        return parse_envelope(data)
    except Exception:
        return None


# Stream B threading — helpers for grouping a flat message list by
# ``thread_root_id``. The feed renderer uses these to collapse a parent
# bubble into a 1-line summary and indent the first N children.
THREAD_VISIBLE_CHILDREN = 3


def group_by_thread(
    messages: list[dict],
) -> dict[str, list[dict]]:
    """Bucket messages by ``thread_root_id``. Messages without a root are
    bucketed under their own ``id`` (each is its own degenerate thread).

    Order within each bucket follows the input order — usually chrono.
    The caller decides how to render each bucket; this is a pure split.
    """
    buckets: dict[str, list[dict]] = {}
    for msg in messages:
        root = (
            msg.get("thread_root_id")
            or msg.get("id")
            or "_unrooted"
        )
        buckets.setdefault(root, []).append(msg)
    return buckets


def thread_summary_line(parent: dict, child_count: int) -> str:
    """1-line rollup of a thread: ``@sender: snippet  · 4 replies``."""
    sender = parent.get("from_name") or parent.get("sender") or "?"
    body = (parent.get("content") or "").replace("\n", " ").strip()
    if len(body) > 60:
        body = body[:59] + "…"
    label = "reply" if child_count == 1 else "replies"
    return f"@{sender}: {body}  ·  {child_count} {label}"


# ── Top app-bar ──────────────────────────────────────────────────────────────


def render_app_bar(
    *,
    room_name: str,
    member_count: int,
    last_active: str,
    console_width: int,
    messages: list[dict] | None = None,
) -> Text:
    """Pinned single-row in-room app-bar.

    Layout:
        ``  <-back  ●  #room   · N members · last-active     ⋯ (s)hare (i)nfo …``

    The leading ``<-`` arrow hints that ``Esc`` returns to welcome. The
    ●/○ presence dot uses the same hash as member avatars but flips to
    bright green when anyone has posted within the last 60s — a quick
    "active now" signal you can read at a glance.
    """
    glyph, glyph_style = ("●", "muted")
    if messages:
        glyph, glyph_style = presence_dot(messages)
    # Avatar dot for the room itself uses the room-name's hashed color
    # — even when the presence ring is dim, the room "identity" reads.
    room_hue = sender_color(room_name)

    left = Text(INDENT)
    left.append("<-", style="dim")
    left.append("  ", style="dim")
    left.append(glyph, style=glyph_style)
    left.append("  ", style="dim")
    left.append(f"#{room_name}", style=f"bold {room_hue}")
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
    # Trailing iMessage-style affordance.
    right.append("tap (i) for info", style="dim italic")
    return two_col(left, right, total_width=max(40, console_width))


# ── Bubble feed ──────────────────────────────────────────────────────────────


def _bubble_width(console_width: int) -> int:
    """Inner text width of a bubble body line. (Back-compat alias.)"""
    return bubble_width(console_width)


def _own_bubble(
    content: str,
    hhmm_str: str,
    console_width: int,
    *,
    receipt: str,  # legacy positional — receipt is now rendered as its own row
    position: str = "only",
    sender: str = "",
    show_header: bool = False,
) -> list[Text]:
    """Right-aligned own-message bubble.

    The receipt parameter is accepted for back-compat but rendered as a
    dedicated micro-row by :func:`render_bubble_feed` — never inline. The
    bubble body itself stays clean: just the message text and a tiny
    timestamp at the right edge of the final line.

    sender + show_header: when True (set by the feed for the FIRST message
    in a same-sender group), render a tiny right-aligned `@<sender> ●`
    header above the bubble — symmetric with :func:`_other_bubble`. Humans
    get a green dot, agents get the hashed sender color. Without this the
    user kept asking "where's my @arav green-dot label?"
    """
    inner = bubble_width(console_width)
    body = _wrap_lines(content, inner)
    out: list[Text] = []
    top_glyph, bot_glyph = bubble_corners(position=position, side="right")

    if show_header and sender:
        color = sender_color(sender)
        glyph_color = "success" if is_human_sender(sender) else color
        head = Text()
        # Right-align: pad to console_width minus the glyph + name + corner.
        name_part = f"@{sender}"
        # Account for: 1 ● + 2 spaces + name + 2 spaces + 1 corner glyph
        used = 1 + 2 + len(name_part) + 2 + 1
        pad = max(2, console_width - 2 - used)
        head.append(" " * pad)
        head.append(f"@{sender}", style=f"bold {color}")
        head.append("  ")
        head.append("●", style=glyph_color)
        head.append("  ")
        head.append(top_glyph, style="dim")
        out.append(head)

    for i, line in enumerate(body):
        is_last = i == len(body) - 1
        suffix = (len(hhmm_str) + 2) if is_last and hhmm_str else 0
        # 2-cell gutter on the right for the rounded-corner glyph.
        pad = max(2, console_width - 2 - len(line) - suffix - 2)
        row = Text(" " * pad)
        # Body — highlighted inline for @mentions / #rooms.
        row.append_text(highlight_inline(line, body_style="bright"))
        if is_last and hhmm_str:
            row.append("  ")
            row.append(hhmm_str, style="ts")
        # Decorative right-edge corner. With a header, the FIRST body line
        # gets a continuation bar (the corner already rendered above);
        # without a header, the first line renders the top corner itself.
        if show_header and sender:
            glyph = bot_glyph if is_last else "│"
        else:
            glyph = top_glyph if i == 0 else (bot_glyph if is_last else "│")
        row.append("  ")
        row.append(glyph, style="dim")
        out.append(row)
    return out


def _other_bubble(
    content: str,
    sender: str,
    hhmm_str: str,
    console_width: int,
    *,
    show_header: bool,
    position: str = "only",
) -> list[Text]:
    """Left-aligned other-person bubble with avatar dot + sender header.

    Humans get a green ● to visually separate them from agents. The
    sender color hashes deterministically from the name (6-color palette
    via :func:`sender_color`). Mentions inside the body are auto-styled.
    """
    inner = bubble_width(console_width)
    body = _wrap_lines(content, inner)
    color = sender_color(sender)
    glyph_color = "success" if is_human_sender(sender) else color
    out: list[Text] = []
    top_glyph, bot_glyph = bubble_corners(position=position, side="left")

    if show_header:
        head = Text(INDENT)
        head.append(top_glyph, style="dim")
        head.append("  ")
        head.append("●", style=glyph_color)
        head.append("  ")
        head.append(f"@{sender}", style=f"bold {color}")
        out.append(head)

    body_indent = "    "
    for i, line in enumerate(body):
        is_first = i == 0
        is_last = i == len(body) - 1
        # Choose left-edge glyph: rounded top on a fresh standalone (no header),
        # continuation bar otherwise. When show_header is True the corner
        # already rendered above, so the first body line gets a continuation.
        if show_header:
            edge = "│" if not is_last else bot_glyph
        else:
            edge = top_glyph if is_first else (bot_glyph if is_last else "│")

        row = Text("  ")
        row.append(edge, style="dim")
        row.append(" ")  # one-cell gutter (replaces the body_indent)
        row.append_text(highlight_inline(line, body_style="bright"))
        if is_last and hhmm_str:
            used = 4 + len(line)
            pad = max(2, console_width - 2 - used - len(hhmm_str))
            row.append(" " * pad)
            row.append(hhmm_str, style="ts")
        out.append(row)
        # Defensive: keep body_indent referenced for any caller that
        # patched the internal layout via a monkeypatch.
        _ = body_indent
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
    """Render a triple-backtick block with subtle background tint."""
    inner = bubble_width(console_width)
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
    """Legacy single-glyph receipt — kept for tests that still poke it."""
    if msg.get("read_by"):
        return "✓✓"
    if msg.get("id") or msg.get("message_id"):
        return "✓"
    return "·"


def _last_own_index(messages: list[dict], my_name: str) -> int:
    """Index of the user's most recent own message; ``-1`` if none."""
    for i in range(len(messages) - 1, -1, -1):
        if (messages[i].get("from_name") or messages[i].get("sender")) == my_name:
            return i
    return -1


def _bubble_position(
    messages: list[dict], idx: int, sender: str, ts: float,
) -> str:
    """Decide whether index *idx* is "only" / "first" / "mid" / "last" in its group.

    Used to pick the correct corner glyphs. A "group" is defined identically
    to :func:`render_bubble_feed`: same sender, < ``GROUP_GAP_S`` seconds.
    """
    def _group_with(j: int) -> bool:
        if not (0 <= j < len(messages)):
            return False
        peer = messages[j]
        peer_sender = peer.get("from_name") or peer.get("sender") or "?"
        if peer_sender != sender:
            return False
        peer_ts = _ts_epoch(peer.get("timestamp", ""))
        return abs(ts - peer_ts) < GROUP_GAP_S

    has_prev = _group_with(idx - 1)
    has_next = _group_with(idx + 1)
    if has_prev and has_next:
        return "mid"
    if has_prev:
        return "last"
    if has_next:
        return "first"
    return "only"


def _verb_summary_body(verb: str, payload: dict) -> str:
    """One-line natural-language summary of a verb payload — what gets put
    INSIDE the bubble. Decoration row above is the styled label; this is
    the readable fallback for SSE-only clients."""
    if verb == "claim":
        return f"claimed {payload.get('task_id', '?')}"
    if verb == "release":
        target = payload.get("handoff_to")
        if target:
            return f"released {payload.get('task_id', '?')} → @{target}"
        return f"released {payload.get('task_id', '?')}"
    if verb == "disagree":
        return str(payload.get("reason", "disagree"))
    if verb == "defer":
        return f"deferring to @{payload.get('to', '?')}"
    if verb == "queue":
        return str(
            payload.get("task_summary")
            or f"queued after #{payload.get('after', '?')}"
        )
    if verb == "vote":
        return f"vote: {payload.get('option', '?')}"
    if verb == "interrupt":
        return str(payload.get("reason", "interrupt"))
    return ""


def render_bubble_feed(
    messages: list[dict],
    room_name: str,
    my_name: str,
    *,
    console_width: int = 80,
    first_unread_index: int | None = None,
    typing: str | None = None,
    reactions_by_index: dict[int, dict[str, int]] | None = None,
) -> list[Text]:
    """Render *messages* as a bubble feed.

    Polish features layered in:

    * Tighter rhythm — exactly 1 blank line between distinct senders, 0
      between same-sender messages within :data:`GROUP_GAP_S`.
    * Time dividers — a centered dim ``— Today, 2:42 PM —`` rule between
      groups separated by ≥5 minutes.
    * Inline ``@mention`` / ``#room`` accent highlighting.
    * Read-receipt row beneath the user's *most recent* own bubble only —
      older own bubbles drop the receipt to reduce noise.
    * Reactions — opt-in via *reactions_by_index*; renders tiny pill chips.
    * Empty-room card — iMessage-y centered illustration.
    """
    if not room_name:
        return _empty_card(
            console_width,
            "No room selected",
            "Press Tab to cycle, or run /new <name>",
        )
    if not messages:
        return empty_room_card(room_name, console_width)

    out: list[Text] = []
    prev_sender: str | None = None
    prev_ts: float = 0.0
    prev_was_system = False
    last_own_idx = _last_own_index(messages, my_name)
    reactions_by_index = reactions_by_index or {}

    for i, msg in enumerate(messages):
        if first_unread_index is not None and i == first_unread_index and i > 0:
            out.append(Text(""))
            out.append(_unread_divider(console_width))
            out.append(Text(""))
            prev_sender = None  # force a fresh header on the next bubble

        sender = msg.get("from_name") or msg.get("sender") or "?"
        content = str(msg.get("content", ""))
        mtype = msg.get("message_type", "chat")
        ts_raw = msg.get("timestamp", "")
        ts = _ts_epoch(ts_raw)
        hhmm_str = _hhmm(ts_raw)

        # System event? Centered + dim.
        if mtype in _SYSTEM_TYPES:
            if out and not prev_was_system:
                out.append(Text(""))
            out.append(_system_event(content, sender, console_width))
            prev_was_system = True
            prev_sender = None
            prev_ts = ts
            continue

        # Social verb? Render decoration above the bubble; replace the bubble
        # body with a natural-language summary so SSE-only clients still see
        # something meaningful even without verb-aware rendering.
        social_decoration: list[Text] = []
        social_summary: str | None = None
        if mtype == "social":
            sv = parse_verb(content)
            if sv is not None:
                payload_dict = (
                    sv.payload if isinstance(sv.payload, dict)
                    else sv.payload.model_dump()
                )
                social_decoration = list(verb_decoration(
                    sv.verb, payload_dict, console_width, sender=sender,
                ))
                social_summary = _verb_summary_body(sv.verb, payload_dict)
                content = social_summary or content

        is_me = sender == my_name
        same_group = (
            sender == prev_sender
            and prev_ts
            and (ts - prev_ts) < GROUP_GAP_S
        )

        # Time-divider — centered between groups separated by ≥5 minutes.
        # Only renders when crossing the boundary, never at the very start
        # of the feed (the empty-room card already orients the user).
        if (
            prev_ts
            and (ts - prev_ts) >= TIME_DIVIDER_GAP_S
            and out
        ):
            label = time_divider_label(ts_raw)
            if label:
                out.append(Text(""))
                out.append(time_divider(label, console_width))
                out.append(Text(""))
                # The divider replaces the usual "blank line between
                # senders" — clear same_group so we don't add another.
                same_group = False
                prev_sender = None
        elif not same_group and out:
            # Tighter rhythm: exactly one blank line between distinct senders.
            out.append(Text(""))

        # Hide HHMM on continuation lines unless we crossed a 5-min boundary.
        show_ts = (not same_group) or ((ts - prev_ts) > TIME_DIVIDER_GAP_S)
        ts_show = hhmm_str if show_ts else ""
        position = _bubble_position(messages, i, sender, ts)

        # Social-verb decoration row(s) go ABOVE the bubble so the verb is
        # legible at a glance. Drop the synthetic interrupt-border marker
        # before printing — its only consumer is structural tests.
        for deco in social_decoration:
            if _INTERRUPT_BORDER_MARKER in deco.plain:
                continue
            out.append(deco)

        for kind, body in _split_code_fences(content):
            if not body and kind == "text":
                continue
            if kind == "code":
                out.extend(_code_block(body, console_width))
                continue
            if is_me:
                out.extend(_own_bubble(
                    body, ts_show, console_width,
                    receipt="",  # receipts are now a dedicated row
                    position=position,
                    sender=sender,
                    show_header=not same_group,
                ))
            else:
                out.extend(_other_bubble(
                    body, sender, ts_show, console_width,
                    show_header=not same_group,
                    position=position,
                ))

        # Reactions row — opt-in, dummy data is fine.
        chips = reactions_by_index.get(i)
        if chips:
            row = reaction_row(chips, console_width)
            if row.cell_len:
                out.append(row)

        # Read-receipt row — only on the user's MOST RECENT own message.
        if is_me and i == last_own_idx:
            label = receipt_label(msg)
            if label:
                out.append(read_receipt_row(label, console_width))

        prev_sender = sender
        prev_ts = ts
        prev_was_system = False

    # Typing indicator — pure visual, no real backend.
    if typing:
        out.append(Text(""))
        out.append(typing_indicator(typing))

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


def render_composer_hint(typing: str | None = None) -> Text:
    """Single dim placeholder rendered just above the prompt.

    When *typing* is provided, a typing-indicator line shows *above* the
    placeholder — the caller passes a renderable group, so we return a
    :class:`Text` that includes both rows separated by a newline.
    """
    line = Text(INDENT)
    line.append("│ ", style="primary")
    line.append("Type a message…  ", style="dim")
    line.append("@", style="kbd")
    line.append(" mention  ", style="dim")
    line.append("/", style="kbd")
    line.append(" command", style="dim")
    if not typing:
        return line
    # When typing is set, prepend the indicator above the placeholder by
    # returning a multi-line Text. Rich prints embedded "\n" correctly.
    indicator = typing_indicator(typing)
    head = Text()
    head.append_text(indicator)
    head.append("\n")
    head.append_text(line)
    return head


__all__ = [
    "GROUP_GAP_S",
    "MAX_FEED_WIDTH_PCT",
    "SHARE_CARD_MIN_W",
    "TIME_DIVIDER_GAP_S",
    "copy_to_clipboard",
    "last_active_label",
    "parse_verb",
    "render_app_bar",
    "render_bubble_feed",
    "render_composer_hint",
    "render_mention_popover",
    "render_share_card",
    "verb_decoration",
]
