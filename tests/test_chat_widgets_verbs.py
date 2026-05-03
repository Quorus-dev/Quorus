"""Tests for the Quorus Social Protocol v1 TUI verb decoration.

Each test calls ``verb_decoration(verb, payload, 80)`` and asserts on the
resulting :class:`rich.text.Text` plain content + style spans. We keep
checks loose-but-meaningful: glyph + key payload tokens + at least one
expected style on the right span.
"""
from __future__ import annotations

from rich.text import Text

from quorus_tui import chat, chat_widgets


def _styles_for(text: Text) -> set[str]:
    """Return the union of styles applied to any span of the Text."""
    return {str(span.style) for span in text.spans if span.style}


def test_verb_decoration_claim_shows_eta():
    rows = chat_widgets.verb_decoration(
        "claim",
        {"task_id": "t-42", "eta_seconds": 600, "scope": "ship"},
        80,
        sender="alice",
    )
    assert len(rows) == 1
    plain = rows[0].plain
    assert "claimed t-42" in plain
    assert "ETA 10m" in plain  # 600s → 10m


def test_verb_decoration_release_handoff_arrow():
    rows = chat_widgets.verb_decoration(
        "release",
        {"task_id": "t-42", "reason": "blocked", "handoff_to": "bob"},
        80,
        sender="alice",
    )
    assert len(rows) == 1
    plain = rows[0].plain
    assert "released t-42" in plain
    assert "→ @bob" in plain


def test_verb_decoration_disagree_blocking_red():
    rows = chat_widgets.verb_decoration(
        "disagree",
        {
            "ref_message_id": "m1",
            "reason": "wrong approach",
            "mode": "blocking",
        },
        80,
        sender="bob",
    )
    assert len(rows) == 1
    plain = rows[0].plain
    assert "disagree (blocking)" in plain
    # The blocking variant must use the danger token in at least one span.
    styles = _styles_for(rows[0])
    assert any("danger" in s for s in styles), styles


def test_verb_decoration_disagree_advisory_yellow():
    rows = chat_widgets.verb_decoration(
        "disagree",
        {"ref_message_id": "m1", "reason": "fyi", "mode": "advisory"},
        80,
        sender="bob",
    )
    assert len(rows) == 1
    plain = rows[0].plain
    assert "disagree (advisory)" in plain
    styles = _styles_for(rows[0])
    # Advisory uses "warning"; must NOT use "danger" anywhere.
    assert any("warning" in s for s in styles), styles
    assert not any("danger" in s for s in styles), styles


def test_verb_decoration_interrupt_emits_border_marker():
    rows = chat_widgets.verb_decoration(
        "interrupt",
        {"ref_message_id": "m1", "reason": "prod is down"},
        80,
        sender="alice",
    )
    assert len(rows) == 2  # caption row + border marker row
    plain = rows[0].plain
    assert "INTERRUPT" in plain
    assert "prod is down" in plain
    # Second row must carry the structural marker that chat.py uses to swap
    # the bubble border to ``danger``.
    assert chat_widgets._INTERRUPT_BORDER_MARKER in rows[1].plain


def test_verb_decoration_defer_arrow_glyph():
    rows = chat_widgets.verb_decoration(
        "defer",
        {"to": "bob", "ttl_seconds": 60},
        80,
        sender="alice",
    )
    assert len(rows) == 1
    plain = rows[0].plain
    assert "deferring to @bob" in plain
    # Single-line; should be visible even on narrow consoles.
    assert plain.lstrip().startswith("↪")


# ---------------------------------------------------------------------------
# Bubble-feed integration: a "social" message must render the decoration row
# above the bubble body (one extra line in the rendered output).
# ---------------------------------------------------------------------------


def test_render_bubble_feed_includes_decoration_for_social_message():
    import json
    envelope = {
        "kind": "social", "verb": "claim", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "ref_message_id": None,
        "payload": {"task_id": "t-42", "eta_seconds": 60, "scope": "x"},
    }
    msgs = [{
        "from_name": "alice",
        "content": json.dumps(envelope),
        "message_type": "social",
        "timestamp": "2026-05-03T08:00:00Z",
    }]
    out = chat.render_bubble_feed(msgs, "test-room", "me", console_width=80)
    rendered = "\n".join(t.plain for t in out)
    assert "claimed t-42" in rendered  # decoration row
    assert "ETA 1m" in rendered
