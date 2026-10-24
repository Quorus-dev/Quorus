"""tui-polish.md §3.2: every theme style reads on dark and light terminals, and
NO_COLOR output carries no colour codes."""
import io
import re

import pytest
from quorus_cli import ui
from rich.console import Console

DARK, LIGHT = (0x1E, 0x1E, 0x1E), (0xFF, 0xFF, 0xFF)
MARK_MIN = 3.0  # WCAG 1.4.11 non-text contrast: glyphs, dots, rules, borders


def _luminance(rgb):
    def chan(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("name", sorted(ui.STYLES))
def test_text_styles_are_the_default_foreground(name):
    """Words (names, rooms, commands, headings, messages): no colour at all,
    only bold/dim/italic, since no colour reaches 4.5:1 on dark and light."""
    style = ui.THEME.styles[name]
    assert style.color is None and style.bgcolor is None, name


@pytest.mark.parametrize("name", sorted(ui.MARKS))
def test_marks_read_on_both_backgrounds(name):
    color = ui.THEME.styles[name].color
    for bg in (DARK, LIGHT):
        assert _contrast(tuple(color.get_truecolor()), bg) >= MARK_MIN, (name, bg)


def _coloured_words(lines):
    """Segments that contain a letter or digit and carry a foreground colour."""
    console = Console(file=io.StringIO(), theme=ui.THEME, force_terminal=True,
                      color_system="truecolor", width=80)
    bad = []
    for line in lines:
        for seg in console.render(line):  # applies each Text's base style too
            if seg.style and seg.style.color is not None and re.search(r"[A-Za-z0-9]",
                                                                      seg.text):
                bad.append((seg.text, str(seg.style)))
    return bad


def test_no_word_in_the_room_is_coloured():
    from quorus_tui import chat, chat_widgets

    msgs = [
        {"from_name": "sam", "content": "why does @sam-opencode fail in #dev?", "id": "1",
         "timestamp": "2027-01-15T08:00:00+00:00"},
        {"from_name": "sam-opencode", "content": "Looking now:\n```\nassert 3\n```\ndone",
         "id": "2", "timestamp": "2027-01-15T08:01:00+00:00"},
        {"from_name": "ada-codex", "content": "", "message_type": "social", "id": "3",
         "timestamp": "2027-01-15T08:02:00+00:00"},
    ]
    lines = chat.render_bubble_feed(msgs, "dev", "sam", console_width=80)
    lines.append(chat.render_app_bar(room_name="dev", member_count=3, last_active="now",
                                     console_width=80, messages=msgs))
    lines.append(chat.render_composer_hint(typing="sam-opencode"))
    for verb, payload in [("vote", {"option": "ship"}), ("disagree", {"reason": "no"}),
                          ("claim", {"task_id": "t1"}), ("interrupt", {"reason": "stop"})]:
        lines += [d for d in chat_widgets.verb_decoration(verb, payload, 80, sender="ada-codex")
                  if chat_widgets._INTERRUPT_BORDER_MARKER not in d.plain]  # never printed
    assert _coloured_words(lines) == []


def test_no_color_emits_no_colour_codes():
    out = io.StringIO()
    console = Console(file=out, theme=ui.THEME, force_terminal=True, no_color=True,
                      highlight=False, width=80)
    console.print("[primary]quorus[/] [muted]0.7.4[/] [agent]@sam[/] [error]x[/] "
                  "[sender3]@ada[/] [ts]16:01[/]")
    text = out.getvalue()
    assert re.sub(r"\x1b\[[0-9;]*m", "", text).strip() == "quorus 0.7.4 @sam x @ada 16:01"
    # SGR colour parameters: 30-38/40-48 (with 38;2/38;5 extended), 90-97, 100-107.
    sgr = re.findall(r"\x1b\[([0-9;]*)m", text)
    for params in sgr:
        for p in filter(None, params.split(";")):
            assert not (30 <= int(p) <= 49 or 90 <= int(p) <= 107), params


def _coloured_words_in_ansi(text):
    """Letters or digits printed while an SGR foreground colour is active."""
    bad, coloured = [], False
    for code, chunk in re.findall(r"\x1b\[([0-9;]*)m|([^\x1b]+)", text):
        if chunk:
            if coloured and re.search(r"[A-Za-z0-9]", chunk):
                bad.append(chunk)
            continue
        params = [int(p) for p in code.split(";") if p] or [0]
        i = 0
        while i < len(params):
            p = params[i]
            if p in (0, 39):
                coloured = False
            elif p in (38,):
                coloured, i = True, i + (4 if params[i + 1:i + 2] == [2] else 2)
            elif 30 <= p <= 37 or 90 <= p <= 97:
                coloured = True
            i += 1
    return bad


def test_printed_screens_colour_no_words(monkeypatch):
    from quorus_tui import welcome

    out = io.StringIO()
    console = Console(file=out, theme=ui.THEME, force_terminal=True,
                      color_system="truecolor", width=100)
    welcome.render_welcome(console, rooms=[{"name": "dev"}], unread_by_room={"dev": 2},
                           selected_room_name="dev", messages=[], agent_name="sam")
    monkeypatch.setattr(ui, "console", console)
    ui.banner(version="9.9.9")
    ui.success("joined #dev")
    ui.error("relay unreachable", hint="run quorus doctor")
    ui.warn("free tier limited")
    assert _coloured_words_in_ansi(out.getvalue()) == []


def test_share_card_and_info_colour_only_their_marks():
    """Codex on 220bd1b4: a base Text style colours every appended span, so the
    share card's brand border coloured its title, code and join command."""
    from quorus_tui import chat_widgets, hub, slash

    card = chat_widgets.render_share_card(room_name="dev", code="ABCD-EFGH",
                                          install_url="https://quorus.dev", console_width=80)
    assert any("ABCD-EFGH" in line.plain for line in card)
    assert _coloured_words(card) == []

    out = io.StringIO()
    console = Console(file=out, theme=ui.THEME, force_terminal=True,
                      color_system="truecolor", width=80)
    state = hub.HubState()
    state.set_rooms([{"name": "dev", "members": ["sam", "sam-opencode"]}])
    state.select_by_name("dev")
    slash.slash_info("", state, "http://r", "s", "sam", console)
    assert "@sam-opencode" in out.getvalue()
    assert _coloured_words_in_ansi(out.getvalue()) == []
