"""tui-polish.md §3.2: every theme style reads on dark and light terminals, and
NO_COLOR output carries no colour codes."""
import io
import re

import pytest
from quorus_cli import ui
from rich.color import ColorType
from rich.console import Console

DARK, LIGHT = (0x1E, 0x1E, 0x1E), (0xFF, 0xFF, 0xFF)
ACCENT_MIN = 3.0  # names, glyphs and headings are bold or large; text is uncoloured


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
def test_each_style_is_default_foreground_or_reads_on_both_backgrounds(name):
    style = ui.THEME.styles[name]
    color = style.color
    if color is None or color.type == ColorType.DEFAULT:
        return  # the terminal's own foreground: legible by definition
    rgb = color.get_truecolor()
    for bg in (DARK, LIGHT):
        assert _contrast(tuple(rgb), bg) >= ACCENT_MIN, (name, color.name, bg)


@pytest.mark.parametrize("name", ["muted", "dim", "ts", "subtle", "bright", "pill_idle"])
def test_text_styles_carry_no_hex(name):
    """Body and meta text: default foreground, at most the dim attribute."""
    assert ui.THEME.styles[name].color is None


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
