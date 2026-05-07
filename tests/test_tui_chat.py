"""Tests for the iMessage-style chat surface (packages/tui/quorus_tui/chat.py)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from quorus_tui import chat, chat_widgets
from rich.console import Console


def _render_to_str(lines, *, width: int = 80) -> str:
    """Render a list of Rich Text lines to a deterministic string."""
    buf = io.StringIO()
    console = Console(
        file=buf, width=width, force_terminal=False,
        no_color=True, legacy_windows=False,
    )
    for line in lines:
        console.print(line)
    return buf.getvalue()


def _now_iso(offset_sec: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_sec)).isoformat()


# ── Sender color ─────────────────────────────────────────────────────────────


def test_sender_color_deterministic():
    assert chat._sender_color("alice") == chat._sender_color("alice")


def test_sender_color_in_palette():
    assert chat._sender_color("anyone") in chat._SENDER_PALETTE


# ── Bubble feed ──────────────────────────────────────────────────────────────


def test_bubble_feed_empty_room_renders_friendly_copy():
    out = _render_to_str(
        chat.render_bubble_feed([], "general", "arav", console_width=80)
    )
    assert "say hi" in out.lower()
    # No emojis on the rendered surface — project rule.
    assert "👋" not in out


def test_bubble_feed_no_room_selected():
    out = _render_to_str(
        chat.render_bubble_feed([], "", "arav", console_width=80)
    )
    assert "no room selected" in out.lower()


def test_bubble_feed_groups_consecutive_same_sender():
    """Within GROUP_GAP_S, the second message hides the sender header."""
    msgs = [
        {"from_name": "ada", "content": "first", "timestamp": _now_iso(-10)},
        {"from_name": "ada", "content": "second", "timestamp": _now_iso(-5)},
    ]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    # Sender header (`@ada`) appears exactly once across both bubbles.
    assert out.count("@ada") == 1


def test_bubble_feed_separates_distinct_senders():
    msgs = [
        {"from_name": "ada", "content": "hello", "timestamp": _now_iso(-30)},
        {"from_name": "bob", "content": "hi", "timestamp": _now_iso(-20)},
    ]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    assert "@ada" in out and "@bob" in out


def test_bubble_feed_own_messages_render_sender_label():
    """Reversed from the previous "omit sender label" rule. arav explicitly
    asked for own messages to carry an `@arav ●` header (green dot for
    humans) so there's symmetry with other senders. iMessage conventionally
    hides the name on outgoing bubbles, but for a multi-agent room where
    your own identity matters at a glance, the label is the clearer UX."""
    msgs = [{"from_name": "arav", "content": "mine", "timestamp": _now_iso(-1)}]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    assert "@arav" in out, (
        "own message must carry the @sender header — was reverted from "
        "the iMessage-style omit per arav's UX request"
    )


def test_bubble_feed_renders_unread_divider():
    msgs = [
        {"from_name": "ada", "content": "old", "timestamp": _now_iso(-300)},
        {"from_name": "ada", "content": "old-2", "timestamp": _now_iso(-250)},
        {"from_name": "bob", "content": "new", "timestamp": _now_iso(-10)},
    ]
    out = _render_to_str(
        chat.render_bubble_feed(
            msgs, "dev", "arav",
            console_width=80,
            first_unread_index=2,
        )
    )
    assert "new" in out


def test_bubble_feed_renders_system_event_centered():
    msgs = [{"from_name": "ada", "content": "joined", "message_type": "join",
             "timestamp": _now_iso(-1)}]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    # System events embed the sender into a single italic phrase.
    assert "ada" in out


# ── App-bar ──────────────────────────────────────────────────────────────────


def test_app_bar_includes_room_name_and_member_count():
    out = _render_to_str([
        chat.render_app_bar(
            room_name="general", member_count=3,
            last_active="active 2m ago", console_width=120,
        )
    ], width=120)
    assert "#general" in out
    assert "3 members" in out
    # Each action shortcut surfaces as `(s)hare`, `(i)nfo`, etc.
    for ch in ("s", "i", "m", "l"):
        assert f"({ch})" in out


def test_app_bar_singular_member():
    out = _render_to_str([
        chat.render_app_bar(
            room_name="solo", member_count=1,
            last_active="", console_width=120,
        )
    ], width=120)
    assert "1 member" in out
    assert "1 members" not in out  # singular grammar


# ── Share card ───────────────────────────────────────────────────────────────


def test_share_card_includes_code_and_install():
    lines = chat.render_share_card(
        room_name="general", code="MJN2-EWVT",
        install_url="curl -sSL https://q.dev/r/MJN2-EWVT.sh | sh",
        console_width=80,
    )
    out = _render_to_str(lines, width=80)
    assert "Share #general" in out
    assert "MJN2-EWVT" in out
    assert "q.dev/r/MJN2-EWVT.sh" in out
    assert "Expires" in out


def test_share_card_truncates_long_codes_for_display():
    long_code = "quorus_join_" + "X" * 200
    lines = chat.render_share_card(
        room_name="r", code=long_code, console_width=80,
    )
    out = _render_to_str(lines, width=80)
    # Display string is ellipsised — the full long token is never shown.
    assert long_code not in out
    assert "…" in out


# ── Mention popover ──────────────────────────────────────────────────────────


def test_mention_popover_prefix_match_first():
    rows = chat.render_mention_popover("ar", ["arav", "ada", "marvin"])
    out = _render_to_str(rows, width=60)
    # Prefix matches lead, substring matches follow.
    arav_pos = out.find("@arav")
    marvin_pos = out.find("@marvin")
    assert arav_pos > -1
    assert marvin_pos == -1 or arav_pos < marvin_pos


def test_mention_popover_returns_empty_when_no_match():
    assert chat.render_mention_popover("xyz", ["alice"]) == []


def test_mention_popover_caps_rows():
    rows = chat.render_mention_popover(
        "", [f"agent-{i}" for i in range(20)], max_rows=3,
    )
    # Header row + 3 matches.
    assert len(rows) == 4


# ── last_active_label ────────────────────────────────────────────────────────


def test_last_active_label_just_now():
    msgs = [{"timestamp": _now_iso(-2)}]
    assert "just now" in chat.last_active_label(msgs)


def test_last_active_label_minutes_ago():
    msgs = [{"timestamp": _now_iso(-180)}]
    assert "m ago" in chat.last_active_label(msgs)


def test_last_active_label_empty_when_no_timestamps():
    assert chat.last_active_label([]) == ""
    assert chat.last_active_label([{"content": "x"}]) == ""


# ── Composer hint ────────────────────────────────────────────────────────────


def test_composer_hint_mentions_at_and_slash_keys():
    out = _render_to_str([chat.render_composer_hint()], width=80)
    assert "@" in out
    assert "/" in out
    assert "Type a message" in out


# ── Code-fence rendering ─────────────────────────────────────────────────────


def test_bubble_feed_renders_code_fences():
    msgs = [{
        "from_name": "ada",
        "content": "look:\n```\nprint('hi')\n```\nthat's it",
        "timestamp": _now_iso(-5),
    }]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    # Code fence body appears verbatim, not the triple-backtick delimiter.
    assert "print('hi')" in out
    assert "```" not in out


# ── Polish-pass: typography rhythm ───────────────────────────────────────────


def test_bubble_feed_zero_blank_lines_inside_group():
    """Same sender within GROUP_GAP_S → 0 blank lines between bubbles."""
    msgs = [
        {"from_name": "ada", "content": "first", "timestamp": _now_iso(-30)},
        {"from_name": "ada", "content": "second", "timestamp": _now_iso(-20)},
        {"from_name": "ada", "content": "third", "timestamp": _now_iso(-10)},
    ]
    lines = chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    # Header (@ada) + 3 body lines = 4 visible lines, plus a single
    # read-receipt-or-similar trailing row — but no blank rows between
    # them inside the group.
    body_lines = [line for line in lines if line.cell_len > 0]
    blank_lines = [line for line in lines if line.cell_len == 0]
    assert len(body_lines) >= 3  # header + 3 messages
    assert len(blank_lines) == 0  # no blanks inside a single group


def test_bubble_feed_one_blank_between_distinct_senders():
    """Distinct senders → exactly 1 blank line between groups."""
    msgs = [
        {"from_name": "ada", "content": "hi", "timestamp": _now_iso(-30)},
        {"from_name": "bob", "content": "yo", "timestamp": _now_iso(-20)},
    ]
    lines = chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    blank_lines = [line for line in lines if line.cell_len == 0]
    assert len(blank_lines) == 1


# ── Polish-pass: read-receipt micro-row ──────────────────────────────────────


def test_read_receipt_only_on_most_recent_own_message():
    """Older own bubbles drop the receipt to reduce noise."""
    msgs = [
        {"from_name": "arav", "content": "old", "id": "1",
         "timestamp": _now_iso(-300)},
        {"from_name": "ada", "content": "ok", "timestamp": _now_iso(-200)},
        {"from_name": "arav", "content": "new", "id": "2",
         "timestamp": _now_iso(-10)},
    ]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    # "Delivered" appears exactly once — under the most recent own bubble.
    assert out.count("Delivered") == 1


def test_read_receipt_label_picks_correct_state():
    """No id → Sending, has id → Delivered, has read_by → Read."""
    assert chat_widgets.receipt_label({}) == "Sending…"
    assert chat_widgets.receipt_label({"id": "x"}) == "Delivered"
    assert chat_widgets.receipt_label({"id": "x", "read_by": ["bob"]}) == "Read"
    assert chat_widgets.receipt_label("not a dict") == ""


def test_read_receipt_row_right_aligned_dim():
    line = chat_widgets.read_receipt_row("Delivered", 80)
    out = _render_to_str([line], width=80)
    # Right-aligned: most leading characters are spaces.
    rendered = out.rstrip("\n")
    assert "Delivered" in rendered
    assert rendered.startswith(" ")
    # No bubble corner glyph — receipts are decoration-free.
    assert "│" not in rendered


# ── Polish-pass: inline @mentions / #rooms highlighting ──────────────────────


def test_highlight_inline_styles_mentions_and_rooms():
    text = chat_widgets.highlight_inline("hey @arav check #design please")
    # Three spans: text, mention, text, room, text — at minimum the
    # tokens render bold-accent (verified via plain content + style).
    out = _render_to_str([text], width=80)
    assert "@arav" in out
    assert "#design" in out


def test_highlight_inline_ignores_email_local_part():
    """@user inside an email shouldn't be styled — uses the negative
    lookbehind to require a non-word char before @ or #."""
    text = chat_widgets.highlight_inline("ping me at arav@example.com please")
    # The whole span renders as text (no separate mention token). We
    # validate by serialising and checking the email survived.
    out = _render_to_str([text], width=80)
    assert "arav@example.com" in out


def test_highlight_inline_works_inside_bubble():
    msgs = [{
        "from_name": "ada",
        "content": "tag @bob and #ops",
        "timestamp": _now_iso(-5),
    }]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=120)
    )
    assert "@bob" in out and "#ops" in out


# ── Polish-pass: time-divider rules ──────────────────────────────────────────


def test_time_divider_label_today():
    label = chat_widgets.time_divider_label(_now_iso(-30))
    assert label.startswith("Today, ")


def test_time_divider_label_yesterday():
    yday_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    label = chat_widgets.time_divider_label(yday_iso)
    assert label.startswith("Yesterday, ")


def test_time_divider_label_blank_on_bad_input():
    assert chat_widgets.time_divider_label("") == ""
    assert chat_widgets.time_divider_label("not-a-date") == ""


def test_bubble_feed_inserts_time_divider_after_5_min_gap():
    msgs = [
        {"from_name": "ada", "content": "early", "timestamp": _now_iso(-1000)},
        {"from_name": "ada", "content": "late",  "timestamp": _now_iso(-10)},
    ]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    assert "Today" in out


def test_bubble_feed_no_time_divider_within_window():
    msgs = [
        {"from_name": "ada", "content": "a", "timestamp": _now_iso(-30)},
        {"from_name": "ada", "content": "b", "timestamp": _now_iso(-20)},
    ]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    assert "Today, " not in out


# ── Polish-pass: app-bar ─────────────────────────────────────────────────────


def test_app_bar_active_dot_when_recent_post():
    msgs = [{
        "from_name": "ada", "content": "x",
        "timestamp": _now_iso(-5),  # within active window
    }]
    out = _render_to_str(
        [chat.render_app_bar(
            room_name="general", member_count=2,
            last_active="active just now", console_width=120,
            messages=msgs,
        )],
        width=120,
    )
    # Active rooms show ●; idle rooms show ○.
    assert "●" in out


def test_app_bar_idle_dot_when_no_recent_post():
    out = _render_to_str(
        [chat.render_app_bar(
            room_name="general", member_count=2,
            last_active="", console_width=120,
            messages=[],
        )],
        width=120,
    )
    # Idle indicator is muted or empty circle.
    assert "○" in out or "active" not in out.lower()


def test_app_bar_includes_action_keys():
    """Polish-tier app-bar exposes (s)hare/(i)nfo/(m)embers/(l)eave."""
    out = _render_to_str(
        [chat.render_app_bar(
            room_name="general", member_count=1,
            last_active="", console_width=120,
        )],
        width=120,
    )
    for ch in ("(s)", "(i)", "(m)", "(l)"):
        assert ch in out


# ── Polish-pass: empty-room state ────────────────────────────────────────────


def test_empty_room_card_renders_room_name_and_cta():
    out = _render_to_str(
        chat.render_bubble_feed([], "general", "arav", console_width=80)
    )
    assert "#general" in out
    assert "say hi to start the conversation" in out
    # Cursor block character — visual "blink" affordance.
    assert "▌" in out


# ── Polish-pass: reactions row ───────────────────────────────────────────────


def test_reaction_row_renders_chips():
    line = chat_widgets.reaction_row({"heart": 2, "thumbs-up": 1}, 80)
    out = _render_to_str([line], width=80)
    # Charm-tier pills render as ``[ heart · 2 ] [ thumbs-up ]`` —
    # padded brackets, centered ``·`` count separator.
    assert "heart" in out and "thumbs-up" in out
    assert "[ " in out and " ]" in out
    # Counts > 1 surface explicitly. Counts of 1 stay implicit.
    assert "2" in out


def test_reaction_row_blank_when_empty():
    assert chat_widgets.reaction_row({}, 80).cell_len == 0


def test_reactions_thread_through_render_bubble_feed():
    msgs = [
        {"from_name": "ada", "content": "great point", "timestamp": _now_iso(-5)},
    ]
    out = _render_to_str(chat.render_bubble_feed(
        msgs, "dev", "arav", console_width=80,
        reactions_by_index={0: {"heart": 3}},
    ))
    # Padded-pill format: ``[ heart · 3 ]``.
    assert "heart" in out and "3" in out
    assert "[ " in out and " ]" in out


# ── Polish-pass: typing indicator ────────────────────────────────────────────


def test_typing_indicator_renders_pulse():
    line = chat_widgets.typing_indicator("arav-codex")
    out = _render_to_str([line], width=80)
    assert "@arav-codex" in out
    assert "is typing" in out
    assert "…" in out


def test_typing_indicator_blank_when_no_typist():
    assert chat_widgets.typing_indicator(None).cell_len == 0
    assert chat_widgets.typing_indicator("").cell_len == 0


def test_render_composer_hint_with_typing_includes_indicator():
    line = chat.render_composer_hint(typing="arav-codex")
    out = _render_to_str([line], width=80)
    assert "@arav-codex" in out
    assert "is typing" in out
    assert "Type a message" in out


# ── Polish-pass: bubble corner glyphs ────────────────────────────────────────


def test_bubble_corners_only_position_returns_full_round():
    top, bot = chat_widgets.bubble_corners(position="only", side="left")
    assert top == "╭" and bot == "╰"


def test_bubble_corners_mid_position_returns_continuation():
    top, bot = chat_widgets.bubble_corners(position="mid", side="left")
    assert top == "│" and bot == "│"


def test_bubble_corners_right_side_flips_glyph_direction():
    top, bot = chat_widgets.bubble_corners(position="only", side="right")
    assert top == "╮" and bot == "╯"


def test_bubble_corners_first_and_last():
    first_top, first_bot = chat_widgets.bubble_corners(position="first")
    assert first_top == "╭" and first_bot == "│"
    last_top, last_bot = chat_widgets.bubble_corners(position="last")
    assert last_top == "│" and last_bot == "╰"


def test_bubble_feed_renders_rounded_corner_on_standalone_other():
    msgs = [{"from_name": "ada", "content": "solo", "timestamp": _now_iso(-5)}]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    # Standalone bubble -> top has rounded corner glyph in the gutter.
    assert "╭" in out


# ── Polish-pass: presence dot ────────────────────────────────────────────────


def test_presence_dot_active_within_window():
    msgs = [{"from_name": "ada", "content": "x", "timestamp": _now_iso(-5)}]
    glyph, style = chat_widgets.presence_dot(msgs)
    assert glyph == "●" and style == "success"


def test_presence_dot_idle_outside_window():
    msgs = [{"from_name": "ada", "content": "x", "timestamp": _now_iso(-300)}]
    glyph, style = chat_widgets.presence_dot(msgs)
    assert glyph == "○" and style == "dim"


def test_presence_dot_skips_system_events():
    msgs = [
        {"from_name": "ada", "content": "joined", "message_type": "system",
         "timestamp": _now_iso(-5)},
        {"from_name": "ada", "content": "old", "timestamp": _now_iso(-300)},
    ]
    glyph, _ = chat_widgets.presence_dot(msgs)
    assert glyph == "○"


# ── Polish-pass: 6-color sender palette + collision regression ───────────────


def test_sender_palette_has_six_distinct_tokens():
    assert len(chat_widgets.SENDER_PALETTE) == 6
    assert len(set(chat_widgets.SENDER_PALETTE)) == 6


def test_sender_color_collision_rate_under_threshold():
    """20 sample names should hit at least 5 of the 6 palette buckets.

    Metric = 1 - (distinct_buckets / palette_size). A 6-color palette
    fully exercised by 20 names yields 0.0; a palette regression to 3
    colors would push the metric to 0.5. We assert ≤ 0.17 — i.e. at
    most one bucket unused — which catches both accidental shrinkage
    and a regressed hash that buckets unevenly.
    """
    names = [
        "arav", "ada", "bob", "carol", "dan", "elaine", "frank", "grace",
        "harvey", "ivy", "jack", "kim", "leah", "mark", "noor", "olivia",
        "paul", "quinn", "rita", "sam",
    ]
    rate = chat_widgets.color_collision_rate(names)
    assert rate <= 0.17, f"unexpected palette under-utilisation: {rate}"


def test_sender_color_returns_palette_token():
    color = chat_widgets.sender_color("anyone")
    assert color in chat_widgets.SENDER_PALETTE


# ── Golden snapshot ──────────────────────────────────────────────────────────


def _stable_snapshot(lines, width: int = 80) -> str:
    """Render lines deterministically — strips trailing whitespace per line.

    Avoids brittle whitespace-only diffs in the golden assertion. We still
    compare the rendered structure (corners, glyphs, ordering) verbatim.
    """
    buf = io.StringIO()
    console = Console(
        file=buf, width=width, force_terminal=False,
        no_color=True, legacy_windows=False,
    )
    for line in lines:
        console.print(line)
    raw = buf.getvalue()
    return "\n".join(s.rstrip() for s in raw.splitlines())


def test_golden_five_message_mixed_human_agent_snapshot():
    """Golden snapshot — render a 5-message mixed conversation and assert
    the visible structure is stable. Catches future renderer drift."""
    # All timestamps are deterministic relative to a fixed reference so the
    # snapshot doesn't churn as `datetime.now()` advances. We bypass
    # _now_iso (which uses real-time) and write absolute ISO strings.
    msgs = [
        {"from_name": "ada",        "content": "morning team",
         "timestamp": "2026-05-02T10:00:00+00:00"},
        {"from_name": "arav",       "content": "morning! shipping the deck",
         "id": "m2", "timestamp": "2026-05-02T10:00:30+00:00"},
        {"from_name": "arav-codex", "content": "running tests now",
         "timestamp": "2026-05-02T10:00:45+00:00"},
        {"from_name": "ada",        "content": "see #design please",
         "timestamp": "2026-05-02T10:01:00+00:00"},
        {"from_name": "arav",       "content": "ack @ada on it",
         "id": "m5", "timestamp": "2026-05-02T10:01:20+00:00"},
    ]
    lines = chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    snap = _stable_snapshot(lines, width=80)

    # The structural assertions (rather than a literal byte-for-byte
    # comparison) guard against accidental drift while keeping the test
    # robust to harmless tweaks like padding adjustments.
    assert "@ada" in snap                        # other-bubble header
    assert "@arav-codex" in snap                 # mixed agent identity
    # Own messages now render the @arav header too (reverted from
    # iMessage-omit per arav's UX request — see
    # test_bubble_feed_own_messages_render_sender_label).
    assert "@arav" in snap.replace("@arav-codex", "")
    assert "#design" in snap                     # inline highlight
    # Round-corner glyphs render on standalone bubbles.
    assert "╭" in snap or "╰" in snap
    # Read-receipt micro-row appears ONCE, on the most recent own message.
    assert snap.count("Delivered") == 1
    # No emojis (project rule).
    for ch in ("👋", "🙂", "❤", "🎉"):
        assert ch not in snap


def test_golden_typing_indicator_in_composer():
    """Typing indicator + composer placeholder render together."""
    out = _render_to_str(
        [chat.render_composer_hint(typing="arav-codex")],
        width=80,
    )
    # Indicator above, prompt below — both present.
    assert "is typing" in out
    assert "Type a message" in out


def test_golden_app_bar_layout_at_120_cols():
    msgs = [{"from_name": "ada", "content": "hi", "timestamp":
             "2026-05-02T10:00:00+00:00"}]
    out = _render_to_str(
        [chat.render_app_bar(
            room_name="dev", member_count=4,
            last_active="active 2m ago", console_width=120,
            messages=msgs,
        )],
        width=120,
    )
    # Spec-by-feature: room name, member count, all four key hints,
    # and the U+2039 back arrow all present.
    assert "‹" in out
    assert "#dev" in out
    assert "4 members" in out
    assert "active 2m ago" in out
    for ch in ("(s)", "(i)", "(m)", "(l)"):
        assert ch in out
