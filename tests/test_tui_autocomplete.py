"""Tests for the slash + @-mention autocomplete popover.

Covers state transitions, filter ranking, navigation, accept / dismiss,
the 8-row visible cap, and the human/agent glyph distinction. Pure
in-process logic — no readchar, no termios, no relay.
"""

from __future__ import annotations

import pytest
from quorus_tui.autocomplete import (
    AutocompletePopover,
    detect_open_trigger,
    mention_items_from_room,
    slash_items_from_registry,
)
from rich.text import Text

# ── Test fixtures ─────────────────────────────────────────────────────────────


_SLASH_REGISTRY = {
    "/help":     ("show keybinds + all commands",        lambda *a: None),
    "/home":     ("return to the welcome view",           lambda *a: None),
    "/rooms":    ("list rooms with numbered menu",        lambda *a: None),
    "/join":     ("/join <room> — switch to a room",      lambda *a: None),
    "/switch":   ("/switch <room> — alias for /join",     lambda *a: None),
    "/new":      ("/new <name> — alias for /create",      lambda *a: None),
    "/create":   ("/create <name> — make a new room",     lambda *a: None),
    "/share":    ("/share <room> — mint a join token",    lambda *a: None),
    "/leave":    ("/leave — exit current room",           lambda *a: None),
    "/delete":   ("/delete <room> — destroy a room",      lambda *a: None),
    "/invite":   ("show how to share the current room",   lambda *a: None),
    "/info":     ("members + last-active for this room",  lambda *a: None),
}


def _make_slash() -> AutocompletePopover:
    items = slash_items_from_registry(_SLASH_REGISTRY)
    return AutocompletePopover(slash_items_provider=lambda: items)


def _make_mention(members: list[str]) -> AutocompletePopover:
    # Treat anyone whose stem == "arav" as the human; everyone else agent.
    def _is_human(name: str) -> bool:
        return name == "arav"

    items = mention_items_from_room(members, is_human=_is_human)
    return AutocompletePopover(mention_items_provider=lambda: items)


# ── Slash open / filter ──────────────────────────────────────────────────────


def test_slash_open_with_no_prefix_shows_all_commands():
    pop = _make_slash()
    pop.open_slash()
    assert pop.is_open
    assert pop.kind == "slash"
    assert pop.prefix == "/"
    # All 12 source items present in registration order.
    labels = [lbl for lbl, _ in pop.items]
    assert labels[0] == "/help"
    assert labels[3] == "/join"
    assert len(pop.items) == 12


def test_slash_filter_by_prefix():
    pop = _make_slash()
    pop.open_slash()
    pop.append_char("j")
    pop.append_char("o")
    labels = [lbl for lbl, _ in pop.items]
    # Prefix-match wins — /join leads. /info has no 'jo' substring at
    # all, so it's absent. /home / /switch likewise filtered out.
    assert labels == ["/join"]
    assert pop.prefix == "/jo"


def test_slash_substring_ranks_after_prefix():
    pop = _make_slash()
    pop.open_slash()
    pop.append_char("e")
    labels = [lbl for lbl, _ in pop.items]
    # Prefix matches first (none start with 'e'), then substrings
    # — /help, /home, /create, /share, /leave, /delete, /invite all
    # contain 'e'. /new also contains 'e'. The exact ordering depends
    # on registry order; assert membership not strict ordering.
    assert "/help" in labels
    assert "/leave" in labels
    assert "/info" not in labels  # /info has no 'e'


# ── Mention open / filter ────────────────────────────────────────────────────


def test_mention_open_shows_room_members():
    pop = _make_mention(["arav", "arav-codex", "arav-claude"])
    pop.open_mention()
    assert pop.kind == "mention"
    labels = [lbl for lbl, _ in pop.items]
    assert labels == ["@arav", "@arav-codex", "@arav-claude"]


def test_mention_filter_by_prefix():
    pop = _make_mention(["arav", "arav-codex", "arav-claude", "ben"])
    pop.open_mention()
    pop.append_char("a")
    labels = [lbl for lbl, _ in pop.items]
    # All three @arav-prefixed entries match; ben filtered out.
    assert "@arav" in labels
    assert "@arav-codex" in labels
    assert "@arav-claude" in labels
    assert "@ben" not in labels


# ── Navigation ───────────────────────────────────────────────────────────────


def test_arrow_keys_navigate_selected_idx():
    pop = _make_slash()
    pop.open_slash()
    assert pop.selected_idx == 0
    assert pop.handle_key("DOWN") == "next"
    assert pop.selected_idx == 1
    assert pop.handle_key("DOWN") == "next"
    assert pop.selected_idx == 2
    assert pop.handle_key("UP") == "prev"
    assert pop.selected_idx == 1
    # Clamp at top.
    pop.handle_key("UP")
    pop.handle_key("UP")
    assert pop.selected_idx == 0
    # Clamp at bottom.
    n = len(pop.items)
    for _ in range(n + 5):
        pop.handle_key("DOWN")
    assert pop.selected_idx == n - 1


# ── Accept / dismiss ─────────────────────────────────────────────────────────


def test_tab_accepts_and_replaces_prefix():
    pop = _make_slash()
    pop.open_slash()
    pop.append_char("j")
    # /jo matches only /join — selected is 0, label is /join.
    pop.append_char("o")
    assert pop.selected_label() == "/join"
    action = pop.handle_key("TAB")
    assert action == "accept"
    # The caller is responsible for splicing — but the popover still
    # exposes the chosen label so the caller can do that work.
    assert pop.items[pop.selected_idx][0] == "/join"


def test_enter_also_accepts():
    pop = _make_slash()
    pop.open_slash()
    assert pop.handle_key("ENTER") == "accept"


def test_esc_dismisses_without_replacing():
    pop = _make_slash()
    pop.open_slash()
    pop.append_char("h")
    assert pop.is_open
    action = pop.handle_key("ESC")
    assert action == "dismiss"
    assert not pop.is_open
    assert pop.kind == "hidden"
    assert pop.items == []


# ── Render cap ───────────────────────────────────────────────────────────────


def test_render_caps_at_8_visible_items():
    pop = _make_slash()
    pop.open_slash()
    rows = pop.render(console_width=80)
    # 1 header + 8 items + 1 footer ('+N more') = 10 total rows.
    assert len(rows) == 10
    # First row is the header containing the kbd hint.
    assert "Tab" in rows[0].plain


def test_render_no_footer_when_within_cap():
    pop = _make_mention(["arav", "ben", "carol"])
    pop.open_mention()
    rows = pop.render(console_width=80)
    # Polish-tier popover: head ``╭ …`` + 3 items + tail ``╰`` = 5 rows.
    # Tail always renders so the popover reads as a closed unit even
    # when there's no overflow text — Charm-style enclosed glyph pair.
    assert len(rows) == 5
    assert rows[0].plain.startswith("  ╭")
    assert rows[-1].plain.startswith("  ╰")


# ── Human / agent distinction ────────────────────────────────────────────────


def test_human_vs_agent_glyph_in_mention_popover():
    pop = _make_mention(["arav", "arav-codex", "arav-claude"])
    pop.open_mention()
    # Description column carries the human/agent annotation.
    descs = {lbl: desc for lbl, desc in pop.items}
    assert descs["@arav"] == "(human)"
    assert descs["@arav-codex"] == "(agent)"
    assert descs["@arav-claude"] == "(agent)"


# ── Word-boundary dismiss ────────────────────────────────────────────────────


def test_popover_dismisses_on_space_after_word():
    pop = _make_slash()
    pop.open_slash()
    pop.append_char("h")
    pop.append_char("e")
    pop.append_char("l")
    pop.append_char("p")
    assert pop.is_open
    action = pop.handle_key(" ")
    assert action == "dismiss"
    assert not pop.is_open


# ── Backspace behavior ───────────────────────────────────────────────────────


def test_backspace_shrinks_then_dismisses_on_sigil():
    pop = _make_slash()
    pop.open_slash()
    pop.append_char("j")
    pop.append_char("o")
    assert pop.prefix == "/jo"
    pop.handle_key("BACKSPACE")
    assert pop.prefix == "/j"
    pop.handle_key("BACKSPACE")
    assert pop.prefix == "/"
    # One more backspace eats the sigil itself → dismiss.
    pop.handle_key("BACKSPACE")
    assert not pop.is_open


# ── detect_open_trigger ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "buf,typed,expected",
    [
        ([], "/", "slash"),               # / at col 0
        ([], "@", "mention"),              # @ at col 0
        (["h", "i", " "], "@", "mention"), # @ after whitespace
        (["h", "i"], "/", "hidden"),       # / mid-word — no
        (["h", "i"], "@", "hidden"),       # @ mid-word — no (email-like)
        (["h", "i", " "], "/", "hidden"),  # / after whitespace — no, only col 0
    ],
)
def test_detect_open_trigger(buf, typed, expected):
    assert detect_open_trigger(buf, typed) == expected


# ── Accept on empty matches dismisses ────────────────────────────────────────


def test_accept_with_no_matches_dismisses():
    pop = _make_slash()
    pop.open_slash()
    pop.append_char("z")
    pop.append_char("z")
    pop.append_char("z")  # no command starts or contains 'zzz'
    assert pop.items == []
    action = pop.handle_key("TAB")
    assert action == "dismiss"
    assert not pop.is_open


# ── Render highlights selection ──────────────────────────────────────────────


def test_render_highlights_selected_row():
    pop = _make_slash()
    pop.open_slash()
    pop.handle_key("DOWN")  # move to /home
    rows = pop.render(console_width=80)
    # Header is rows[0]; items start at rows[1]. Selected row is /home
    # (index 1 in items → rows[2]).
    selected_row = rows[2]
    # Polish-tier rows lead with a continuation gutter ``│ `` (matching
    # the bubble side-bar). Selected rows still carry the ``> `` marker.
    assert "> " in selected_row.plain
    assert selected_row.plain.lstrip().startswith("│")
    # Non-selected rows do not have the > marker.
    assert "> " not in rows[1].plain


# ── Render shows `(no matches)` when filter has zero hits ───────────────────


def test_render_shows_no_matches_hint():
    pop = _make_slash()
    pop.open_slash()
    pop.append_char("z")
    pop.append_char("z")
    rows = pop.render(console_width=80)
    assert len(rows) == 1
    assert "(no matches)" in rows[0].plain


# ── Window scroll keeps selection in view ────────────────────────────────────


def test_scroll_window_slides_to_keep_selection_visible():
    pop = _make_slash()
    pop.open_slash()
    # Drive selection past the visible window (>= 8).
    for _ in range(10):
        pop.handle_key("DOWN")
    rows = pop.render(console_width=80)
    # Selected row must be inside the rendered slice.
    plain_lines = [r.plain for r in rows]
    selected_label = pop.selected_label()
    assert any(selected_label in line for line in plain_lines)


# ── slash_items_from_registry shape ──────────────────────────────────────────


def test_slash_items_from_registry_strips_handler():
    items = slash_items_from_registry(_SLASH_REGISTRY)
    # 12 items, each a 2-tuple of (verb, description).
    assert len(items) == 12
    for verb, desc in items:
        assert verb.startswith("/")
        assert isinstance(desc, str)


# ── mention_items_from_room handles empty / blank names ──────────────────────


def test_mention_items_from_room_skips_blanks():
    items = mention_items_from_room(
        ["arav", "", "ben"], is_human=lambda _: False,
    )
    labels = [lbl for lbl, _ in items]
    assert labels == ["@arav", "@ben"]


# ── Render rows are Rich Text instances ──────────────────────────────────────


def test_render_returns_rich_text_rows():
    pop = _make_slash()
    pop.open_slash()
    rows = pop.render(console_width=80)
    for row in rows:
        assert isinstance(row, Text)
