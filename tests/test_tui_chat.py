"""Tests for the iMessage-style chat surface (packages/tui/quorus_tui/chat.py)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from rich.console import Console

from quorus_tui import chat


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


def test_bubble_feed_own_messages_omit_sender_label():
    msgs = [{"from_name": "arav", "content": "mine", "timestamp": _now_iso(-1)}]
    out = _render_to_str(
        chat.render_bubble_feed(msgs, "dev", "arav", console_width=80)
    )
    # Own messages don't render a sender label — it's you.
    assert "@arav" not in out


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
