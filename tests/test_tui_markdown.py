"""Agent replies are markdown; the chat showed `**bold**` and `## heads` raw."""

from quorus_tui.chat import render_bubble_feed
from quorus_tui.chat_widgets import plain_markdown


def test_plain_markdown_keeps_words_drops_syntax() -> None:
    src = ("## Summary\n- fixed **add()** in `calc.py`\n* tests: 3 passed\n"
           "see [PR](https://x.dev/1)")
    assert plain_markdown(src) == ("Summary\n• fixed add() in calc.py\n• tests: 3 passed\n"
                                   "see PR (https://x.dev/1)")


def test_plain_markdown_leaves_ordinary_text_alone() -> None:
    for text in ("a * b = c", "snake_case_name", "2 ** 3", "@arav-claude #build",
                 "use __init__.py", "x - y"):
        assert plain_markdown(text) == text


def test_chat_feed_renders_agent_markdown_clean() -> None:
    msg = {"from_name": "a-claude", "content": "**Done.** Fixed `calc.py`",
           "timestamp": "2026-10-16T12:00:00Z", "message_type": "chat"}
    rows = render_bubble_feed([msg], "build", "arav", console_width=100)
    text = "\n".join(r.plain for r in rows)
    assert "Done. Fixed calc.py" in text and "**" not in text and "`" not in text
