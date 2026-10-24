"""tui-polish.md defect 3: an agent reply with a code fence is one message, not
several: one sender header, the code inside it, one timestamp, one pair of corners."""
from quorus_tui import chat

REPLY = ("Looking now. The failure is in test_parse.py:\n```\nAssertionError: expected 3\n```\n"
         "fix: change split(',', 2) to split(',')")


def _plain(lines):
    return [line.plain for line in lines]


def _feed(content, sender="sam-opencode", me="sam"):
    msg = {"from_name": sender, "content": content, "id": "m1",
           "timestamp": "2027-01-15T08:00:00+00:00"}
    return _plain(chat.render_bubble_feed([msg], "general", me, console_width=80))


def test_a_reply_with_a_code_fence_renders_one_header():
    rows = _feed(REPLY)
    assert sum("@sam-opencode" in r for r in rows) == 1
    assert sum("╭" in r for r in rows) == 1 and sum("╰" in r for r in rows) == 1
    code = [r for r in rows if "AssertionError" in r]
    assert len(code) == 1 and code[0].startswith("  │   AssertionError")
    stamped = [r for r in rows if r.rstrip().endswith(chat._hhmm(
        "2027-01-15T08:00:00+00:00"))]
    assert len(stamped) == 1 and "split(',')" in stamped[0]


def test_own_reply_with_code_is_one_block_too():
    rows = _feed(REPLY, sender="sam", me="sam")
    assert sum("@sam" in r for r in rows) == 1
    assert sum("╮" in r for r in rows) == 1 and sum("╯" in r for r in rows) == 1


def test_a_code_only_reply_still_names_its_sender():
    rows = _feed("```\nprint('hi')\n```")
    assert sum("@sam-opencode" in r for r in rows) == 1
    assert any("print('hi')" in r for r in rows)


def test_part_positions_open_and_close_once():
    assert chat._part_position("only", True, False) == "first"
    assert chat._part_position("only", False, False) == "mid"
    assert chat._part_position("only", False, True) == "last"
    assert chat._part_position("only", True, True) == "only"
    assert chat._part_position("mid", True, True) == "mid"
    assert chat._part_position("first", False, True) == "mid"
