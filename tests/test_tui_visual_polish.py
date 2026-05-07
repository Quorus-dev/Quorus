"""Charm-tier visual polish — render tests for the TUI surface.

Covers the polish-pass spec (2026-05-03 Plan v8):
  * Curated 6-color sender palette
  * Time-divider hairline + dim italic typography
  * Presence-dot pulse glyph for typing state
  * Daemon-status indicator on the app-bar
  * Reaction-pill padded bracket format
  * Autocomplete popover bubble-glyph parity
  * Unread divider hairline matching time-divider style
  * System-event ``·  text  ·`` quiet-aside framing

Pure render — every test renders a small Rich snippet to a string via
the same ``no_color=True`` console pattern as ``test_tui_chat.py`` so
the assertions are deterministic across terminals.
"""

from __future__ import annotations

import io

from quorus_tui import autocomplete as _ac
from quorus_tui import chat, chat_widgets
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


# ── A. Bubble color palette curation ─────────────────────────────────────────


def test_bubble_color_palette_curated():
    """SENDER_PALETTE is exactly the 6 curated theme tokens, in order.

    Curation order is load-bearing: the hash bucket lookup uses index
    parity, so swapping two slots changes everyone's color. We pin the
    spec here so a regression (e.g. trimming back to 3 hues) fails loudly.
    """
    expected = (
        "sender1",  # teal-300
        "sender2",  # indigo-300
        "sender3",  # amber-300
        "sender4",  # rose-300
        "sender5",  # violet-300
        "sender6",  # cyan-300
    )
    assert chat_widgets.SENDER_PALETTE == expected
    # Backwards-compat alias must point at the same tuple.
    assert chat_widgets.CURATED_SENDER_PALETTE is chat_widgets.SENDER_PALETTE
    # Every sender_color() return must be drawn from the palette.
    for name in ("arav", "ada", "ben", "carol", "dax", "eve", "frank", "grace"):
        assert chat_widgets.sender_color(name) in expected


def test_bubble_color_collision_rate_under_20pct_for_typical_room():
    """A 12-member room should hit ≥5 of 6 palette buckets (≤17% collision)."""
    members = [
        "arav", "ada", "ben", "carol", "dax", "eve",
        "frank", "grace", "heidi", "ivan", "jack", "kim",
    ]
    rate = chat_widgets.color_collision_rate(members)
    # 1 - (5/6) = 0.166… ; we accept up to 0.20 to absorb hash skew.
    assert rate <= 0.20, f"palette under-utilised: collision={rate:.3f}"


# ── B. Time-divider hairline + spacing ───────────────────────────────────────


def test_time_divider_uses_dim_with_line_glyphs():
    """Divider renders ``──  Today, …  ──`` with U+2500 hairlines."""
    line = chat_widgets.time_divider("Today, 2:42 PM", console_width=80)
    out = _render_to_str([line], width=80)
    # U+2500 (BOX DRAWINGS LIGHT HORIZONTAL) on both sides of the label.
    assert "─" in out
    # Label is preserved with the two-space gutter on each side.
    assert "  Today, 2:42 PM  " in out
    # Indent is exactly two spaces (project convention).
    assert out.startswith("  ─")


def test_time_divider_empty_label_returns_blank():
    """Empty label degrades to a blank Text — no orphan dashes."""
    line = chat_widgets.time_divider("", console_width=80)
    assert line.cell_len == 0


# ── C. Presence-dot pulse glyph ──────────────────────────────────────────────


def test_presence_dot_pulses_when_typing():
    """Typing flips presence to the half-filled ``◐`` "soft pulse" glyph."""
    glyph_idle, style_idle = chat_widgets.presence_dot([])
    glyph_typing, style_typing = chat_widgets.presence_dot([], typing="ada")
    assert glyph_idle == chat_widgets.PRESENCE_GLYPH_IDLE
    assert glyph_typing == chat_widgets.PRESENCE_GLYPH_TYPING
    # Idle is dim, typing is the brand accent — eye reads "in motion".
    assert style_idle == "dim"
    assert style_typing == "accent"


def test_presence_dot_active_when_recent_post():
    """Recent post (within window) yields the filled ``●`` in success."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    msgs = [{"from_name": "ada", "content": "hi",
             "timestamp": now.isoformat()}]
    glyph, style = chat_widgets.presence_dot(msgs, now=now)
    assert glyph == chat_widgets.PRESENCE_GLYPH_ACTIVE
    assert style == "success"


def test_daemon_status_glyph_mirrors_presence_family():
    """Daemon dot uses the same glyph family as presence — visual parity."""
    on = chat_widgets.daemon_status_glyph(True)
    off = chat_widgets.daemon_status_glyph(False)
    assert on == (chat_widgets.PRESENCE_GLYPH_ACTIVE, "success")
    assert off == (chat_widgets.PRESENCE_GLYPH_IDLE, "dim")


# ── D. App-bar daemon status integration ─────────────────────────────────────


def test_app_bar_renders_daemon_status_when_provided():
    """``daemon_connected`` adds a tiny ``● live`` indicator on the right."""
    line = chat.render_app_bar(
        room_name="dev", member_count=3, last_active="",
        console_width=120, daemon_connected=True,
    )
    out = _render_to_str([line], width=120)
    assert "live" in out
    # Glyph appears before "live" on the right side.
    assert "● live" in out or "●  live" in out


def test_app_bar_renders_offline_daemon_status():
    line = chat.render_app_bar(
        room_name="dev", member_count=3, last_active="",
        console_width=120, daemon_connected=False,
    )
    out = _render_to_str([line], width=120)
    assert "offline" in out


def test_app_bar_omits_daemon_status_when_unspecified():
    """Backward-compat: without ``daemon_connected`` the indicator is absent."""
    line = chat.render_app_bar(
        room_name="dev", member_count=3, last_active="",
        console_width=120,
    )
    out = _render_to_str([line], width=120)
    assert "live" not in out
    assert "offline" not in out


def test_app_bar_typing_state_pulses_room_dot():
    """When ``typing`` is set, the room dot becomes the half-filled glyph."""
    line = chat.render_app_bar(
        room_name="dev", member_count=2, last_active="",
        console_width=120, typing="ada",
    )
    out = _render_to_str([line], width=120)
    assert chat_widgets.PRESENCE_GLYPH_TYPING in out


# ── E. Reaction pills (padded bracket Charm style) ───────────────────────────


def test_reaction_pills_padded_bracket_format():
    """Pills render as ``[ heart · 2 ] [ thumbs-up ]`` — padded brackets."""
    line = chat_widgets.reaction_row({"heart": 2, "thumbs-up": 1}, 80)
    out = _render_to_str([line], width=80)
    # Padded bracket on both ends.
    assert "[ heart" in out
    assert " ]" in out
    # Centered ``·`` separator between label and count, count > 1 only.
    assert "heart · 2" in out
    # Single-count chip drops the ``· N`` so it stays tight.
    assert "thumbs-up ]" in out
    assert "thumbs-up · 1" not in out


# ── F. Autocomplete popover bubble-glyph parity ─────────────────────────────


def test_autocomplete_popover_uses_bubble_corner_glyphs():
    """Popover head + tail use ``╭`` and ``╰`` — same as bubble corners."""
    pop = _ac.AutocompletePopover(
        slash_items_provider=lambda: [
            ("/help", "show help"),
            ("/join", "join a room"),
        ],
    )
    pop.open_slash()
    rows = pop.render(console_width=80)
    head_text = rows[0].plain
    tail_text = rows[-1].plain
    assert "╭" in head_text
    assert "╰" in tail_text
    # Same glyph family as bubble_corners() returns for left-side bubbles.
    left_top, left_bot = chat_widgets.bubble_corners(position="only", side="left")
    assert left_top == "╭"
    assert left_bot == "╰"


def test_autocomplete_item_rows_lead_with_continuation_bar():
    """Item rows show a ``│`` gutter mirroring the bubble side-bar."""
    pop = _ac.AutocompletePopover(
        slash_items_provider=lambda: [("/help", "show help")],
    )
    pop.open_slash()
    rows = pop.render(console_width=80)
    # Row[0] = head, row[1] = item (selected by default), row[2] = tail.
    item_row = rows[1]
    assert "│" in item_row.plain


# ── G. Unread divider — hairline parity with time-divider ───────────────────


def test_unread_divider_uses_hairline_glyphs():
    """Unread cutoff renders ``──  new  ──`` matching the time-divider rule."""
    msgs = [
        {"from_name": "ada", "content": "hi", "timestamp": "2026-05-02T10:00:00+00:00"},
        {"from_name": "ben", "content": "yo", "timestamp": "2026-05-02T10:01:00+00:00"},
    ]
    out = _render_to_str(chat.render_bubble_feed(
        msgs, "dev", "arav", console_width=80, first_unread_index=1,
    ))
    assert "─" in out
    assert "new" in out


# ── H. System-event quiet-aside framing ──────────────────────────────────────


def test_system_event_wraps_with_middle_dots():
    """System events render ``·  ada joined  ·`` so they read as quiet asides."""
    msgs = [{
        "from_name": "ada", "content": "joined", "message_type": "join",
        "timestamp": "2026-05-02T10:00:00+00:00",
    }]
    out = _render_to_str(chat.render_bubble_feed(
        msgs, "dev", "arav", console_width=80,
    ))
    # Centered with leading + trailing middle-dots.
    assert "·  ada joined  ·" in out


# ── I. Composer hint typography ──────────────────────────────────────────────


def test_composer_hint_renders_kbd_hints_separately():
    """Composer shows ``@ mention    / command`` with keyboard hints set apart."""
    line = chat.render_composer_hint()
    out = _render_to_str([line], width=80)
    assert "Type a message" in out
    assert "@ mention" in out
    assert "/ command" in out


# ── J. Snapshot — full app-bar render at 80 cols ────────────────────────────


def test_snapshot_app_bar_typical_room_at_80_cols():
    """Snapshot — `dev` room, 3 members, active 2m, daemon live, 80 cols.

    A rendering regression on this surface is the most likely visible
    bug. We pin the *shape* (glyphs + key tokens), not the exact spacing,
    so terminal-width tweaks don't force a snapshot bump every time.
    """
    line = chat.render_app_bar(
        room_name="dev", member_count=3,
        last_active="active 2m ago", console_width=80,
        daemon_connected=True,
    )
    out = _render_to_str([line], width=80)
    # Required tokens for the polish-tier app-bar.
    expected_fragments = (
        "‹",                    # back-arrow glyph
        "#dev",                 # room name
        "3 members",            # member-count phrasing
        "active 2m ago",        # last-active phrasing
        "live",                 # daemon-status copy
        "(s)hare",              # action affordance
        "(i)nfo",
        "(m)embers",
        "(l)eave",
    )
    for frag in expected_fragments:
        assert frag in out, f"expected fragment missing: {frag!r}\n--- render ---\n{out}"
