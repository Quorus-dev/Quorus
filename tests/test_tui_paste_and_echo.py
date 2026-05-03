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


def test_send_and_echo_both_use_agent_name() -> None:
    """The send path AND the optimistic echo MUST use agent_name.

    Why this rule (reversed from the earlier chat_identity attempt):
    the relay enforces JWT.sub == from_name with `Cannot send as another
    user` (403). chat_identity is suffix-stripped from agent_name for
    DISPLAY ONLY (header, glyph) — using it on the wire makes the
    relay reject every send and the user gets "Couldn't reach the
    relay." The local echo must also use agent_name so it MATCHES the
    SSE round-trip (otherwise the bubble flickers when the real
    message replaces the echo).

    Identity disambiguation across the wire requires relay support for
    tenant aliases (a Participant.aliases field that the relay accepts
    in from_name). Until then, agent_name is the only safe value.
    """
    src = inspect.getsource(tui_hub)
    anchor = "Optimistic local echo"
    assert anchor in src, "echo block comment moved — re-pin the test"
    block_start = src.index(anchor)
    block = src[block_start : block_start + 800]
    # echo dict's from_name must reference agent_name, not chat_identity.
    # The first occurrence of either inside the block represents the
    # `from_name: ...` value (the echo dict is the only assignment in
    # this region).
    agent_idx = block.find("agent_name")
    chat_idx = block.find("chat_identity")
    assert agent_idx != -1, (
        "agent_name reference missing from echo block — "
        "regressed to a chat_identity send that the relay 403s"
    )
    if chat_idx != -1:
        assert agent_idx < chat_idx, (
            "chat_identity appears before agent_name in echo block — "
            "the relay rejects from_name != JWT.sub. agent_name must "
            "be the from_name value; chat_identity is display-only."
        )


def _find_submit_enter_block(src: str) -> str:
    """Return the slice of source containing the SUBMIT-mode ENTER handler.

    The autocomplete popover added a SECOND ENTER branch at the top of
    `_read_input` that intercepts ENTER while the popover is open. The
    submit-mode handler lives further down. We locate it by anchoring on
    the paste-detection comment that's only in the submit branch.
    """
    paste_anchor = "Multi-line paste detection"
    assert paste_anchor in src, "paste-detect comment moved or removed"
    block_start = src.index(paste_anchor)
    return src[block_start : block_start + 1200]


def test_enter_handler_peeks_for_more_bytes() -> None:
    """The submit-mode ENTER branch must call select() before submitting.

    Multi-line paste arrived as N successive `\\n` bytes. Without a
    peek, each one hit the submit path. With the peek (5ms timeout),
    paste bytes are detected and appended to the buf instead.
    """
    src = inspect.getsource(tui_hub)
    block = _find_submit_enter_block(src)
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
    block = _find_submit_enter_block(src)
    assert 'buf.append("\\n")' in block, (
        "buf.append('\\\\n') missing from paste path — pasted newlines "
        "will be silently dropped instead of preserved in the message"
    )
