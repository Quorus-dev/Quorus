"""tui-polish.md defect 3: an agent reply with a code fence is one message, not
several: the sender header first, the code inside, one pair of corners and one
timestamp on the message's last line, wherever the code sits."""
import pytest
from quorus_tui import chat

TS = "2027-01-15T08:00:00+00:00"
FENCE = "```\nAssertionError: expected 3\n```"
SHAPES = {
    "code-middle": f"Looking now:\n{FENCE}\nfix: split(',')",
    "code-first": f"{FENCE}\nfix: split(',')",
    "code-last": f"Looking now:\n{FENCE}",
    "code-only": FENCE,
}


def _rows(content, sender):
    msg = {"from_name": sender, "content": content, "id": "m1", "timestamp": TS}
    lines = chat.render_bubble_feed([msg], "general", "sam", console_width=80)
    return [line.plain for line in lines if line.plain.strip()]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("sender,top,bottom", [("sam-opencode", "╭", "╰"), ("sam", "╮", "╯")])
def test_a_reply_with_code_is_one_block(shape, sender, top, bottom):
    rows = _rows(SHAPES[shape], sender)
    assert f"@{sender}" in rows[0] and top in rows[0]  # header first, before any code
    assert sum(f"@{sender}" in r for r in rows) == 1
    assert sum(top in r for r in rows) == 1 and sum(bottom in r for r in rows) == 1
    assert bottom in rows[-1] and chat._hhmm(TS) in rows[-1]  # closes on the last line
    assert sum(chat._hhmm(TS) in r for r in rows) == 1
    code = [r for r in rows if "AssertionError" in r]
    assert len(code) == 1 and "  AssertionError" in code[0]


def test_code_sits_on_the_message_gutter():
    rows = _rows(SHAPES["code-middle"], "sam-opencode")
    assert rows[1] == "  │ Looking now:"
    assert rows[2] == "  │   AssertionError: expected 3"
    assert rows[3].startswith("  ╰ fix: split(',')")
