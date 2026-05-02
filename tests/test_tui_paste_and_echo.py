"""Regression tests for two TUI bugs the user reported on 2026-05-02.

Bug A: pasting a 19-line message produced 19 separate room messages.
       Cause: every `\\n` byte in the paste hit the ENTER branch, which
       called .strip() and submitted, so each line became its own send.
       Fix: peek the stdin buffer when ENTER fires; if more bytes are
       waiting (paste signature), append `\\n` to the buf and keep
       reading.

Bug B: the user's optimistic local echo tagged messages with `agent_name`
       (`arav-codex`) instead of `chat_identity` (`arav`), so the user
       saw their own messages as `@arav-codex` even though the relay
       and every other client correctly received them as `@arav`.
       Fix: use chat_identity for the echo's from_name field.

These tests pin the actual call sites in hub.py via inspect.getsource so
we catch any future regression that swaps the variable name back.
"""

from __future__ import annotations

import inspect

from quorus import tui_hub


def test_local_echo_uses_chat_identity_not_agent_name() -> None:
    """The optimistic echo dict's from_name must come from chat_identity.

    Reading the real source instead of running the loop because the loop
    is a giant cbreak-mode reader that's not unit-testable. The test acts
    as a live spec: if anyone replaces `chat_identity` with `agent_name`
    in the echo, this fails loud.
    """
    src = inspect.getsource(tui_hub)
    # Find the echo dict near the optimistic-echo comment and verify it
    # references chat_identity. Locating by the surrounding comment text
    # keeps the test stable across line moves.
    anchor = "Optimistic local echo"
    assert anchor in src, "echo block comment moved — re-pin the test"
    block_start = src.index(anchor)
    # Look in the next 800 chars for the echo dict; chat_identity must
    # appear before agent_name there (the echo block sets from_name).
    block = src[block_start : block_start + 800]
    chat_idx = block.find("chat_identity")
    agent_idx = block.find("agent_name")
    assert chat_idx != -1, (
        "chat_identity reference removed from echo block — "
        "user will see own messages tagged as the agent again"
    )
    assert chat_idx < agent_idx or agent_idx == -1, (
        "agent_name appears before chat_identity in echo block — "
        "this is exactly the bug we fixed; revert"
    )


def test_enter_handler_peeks_for_more_bytes() -> None:
    """The ENTER branch must call select() before submitting.

    Multi-line paste arrived as N successive `\\n` bytes. Without a
    peek, each one hit the submit path. With the peek (5ms timeout),
    paste bytes are detected and appended to the buf instead.
    """
    src = inspect.getsource(tui_hub)
    # Locate the enter handler by looking for the readchar.key.ENTER tuple
    enter_anchor = 'if key in (readchar.key.ENTER, "\\r", "\\n"):'
    assert enter_anchor in src, "enter handler comment/key tuple moved"
    block_start = src.index(enter_anchor)
    block = src[block_start : block_start + 1200]
    # The peek call must appear before the _PENDING_INPUT_BUF.clear()
    # commit. Otherwise the paste-handling is dead code.
    peek_call = "_sel.select([sys.stdin], [], [], 0.005)"
    commit_call = "_PENDING_INPUT_BUF.clear()"
    assert peek_call in block, (
        "5ms-peek for paste detection removed — multi-line pastes will "
        "fragment back into N separate messages"
    )
    assert block.index(peek_call) < block.index(commit_call), (
        "peek happens AFTER commit — paste fragmentation will return"
    )


def test_enter_handler_appends_newline_when_more_pending() -> None:
    """When the peek finds more bytes, the handler must append `\\n` to buf."""
    src = inspect.getsource(tui_hub)
    enter_anchor = 'if key in (readchar.key.ENTER, "\\r", "\\n"):'
    block_start = src.index(enter_anchor)
    block = src[block_start : block_start + 1200]
    assert 'buf.append("\\n")' in block, (
        "buf.append('\\\\n') missing from paste path — pasted newlines "
        "will be silently dropped instead of preserved in the message"
    )
