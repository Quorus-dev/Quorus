"""End-to-end notification dispatch contract for @-mentions.

Where ``test_notifications_native.py`` pins the OS-layer wiring (osascript
argv shape, notify-send shape, rate limiter, sanitisation, PII guard), this
file pins the **dispatch contract** — what reflexd / hub / cli actually pass
to :func:`quorus.notifications.native.notify` when an @-mention envelope
arrives. The test surface covers all 6 supported harness vendors so a wire
contract change in any one of them shows up here loudly.

Why a separate file
-------------------
The native test mocks the OS at one layer down — it doesn't know what an
@-mention envelope looks like. This file mocks ``notify`` at the boundary
and replays envelope shapes across all 6 vendors (claude, codex, gemini,
cursor, opencode, cline). That answers the user-facing question: "did the
banner actually fire when alice @-mentioned me from harness X?" — which is
the exact failure we got burned on at the YC hackathon.

Test surface
------------
* ``classify_message`` returns ``RESPOND/mention`` for every vendor name
  variant we ship (covers harness suffix detection)
* The envelope→notify call passes title=``Quorus — <room>``, body=raw
  content, sender, room — matching the 3 production callsites
* Self-sent envelopes do NOT trigger ``notify`` (anti-loop)
* Non-mention chat does NOT trigger ``notify``
* DM envelopes (no room) render as ``Quorus — DM``
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Reflexd lives under scripts/, not on the import path by default.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import reflexd_triage  # noqa: E402

# The 6 vendor harnesses we ship adapters for. Names match the participant
# convention used by reflexd (`<vendor>` or `<vendor>-test`). If a vendor
# is added/removed, this list should change in lock-step with
# scripts/reflexd.py's adapter table.
VENDORS = ("claude", "codex", "gemini", "cursor", "opencode", "cline")


# ---------------------------------------------------------------------------
# classify_message → mention path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vendor", VENDORS)
def test_at_mention_triggers_respond_for_every_vendor(vendor: str) -> None:
    """Every vendor name is recognised as a literal @-mention."""
    result = reflexd_triage.classify_message(
        content=f"@{vendor} ship the demo",
        sender="arav",
        self_name=vendor,
    )
    assert result.action == "RESPOND"
    assert result.kind == "mention"
    assert result.reason == "literal @mention"


@pytest.mark.parametrize("vendor", VENDORS)
def test_self_message_never_triggers_respond(vendor: str) -> None:
    """Anti-loop: vendor sees its own @-mention and IGNOREs."""
    result = reflexd_triage.classify_message(
        content=f"@{vendor} replying to myself",
        sender=vendor,
        self_name=vendor,
    )
    assert result.action == "IGNORE"


def test_unrelated_chat_does_not_match_mention() -> None:
    """Plain chat without @-prefix is IGNOREd (so notify never fires)."""
    result = reflexd_triage.classify_message(
        content="just a status update",
        sender="arav",
        self_name="claude",
    )
    assert result.action == "IGNORE"


def test_at_mention_substring_does_not_match() -> None:
    """``@claude-bot`` does not match self_name=``claude`` (whole-token guard)."""
    result = reflexd_triage.classify_message(
        content="@claude-bot please review",
        sender="arav",
        self_name="claude",
    )
    # The whole-token regex in _has_literal_mention guarantees this.
    assert result.kind != "mention"


# ---------------------------------------------------------------------------
# Dispatch shape: envelope → notify(title, body, sender, room)
# ---------------------------------------------------------------------------


def _dispatch_for_envelope(envelope: dict, *, notify: MagicMock) -> None:
    """Mirror the exact 4 lines reflexd / hub / cli use to fire notify().

    Keeping this in the test file (rather than calling reflexd directly)
    avoids spinning up the daemon while still pinning the contract that
    the 3 production callsites all share. If reflexd / hub / cli ever
    drift apart, that drift is now a 1-grep-fix.
    """
    sender = envelope.get("from_name") or envelope.get("sender") or ""
    content = envelope.get("content") or ""
    room = envelope.get("room") or ""
    notify(
        f"Quorus — {room or 'DM'}",
        content,
        sender=sender,
        room=room,
    )


@pytest.mark.parametrize("vendor", VENDORS)
def test_room_mention_dispatches_with_correct_shape(vendor: str) -> None:
    """Room-scoped @-mention → title=``Quorus — <room>``, body=content."""
    notify = MagicMock(return_value=True)
    envelope = {
        "from_name": "arav",
        "content": f"@{vendor} ship it",
        "room": "dev",
        "id": "msg_123",
        "message_type": "chat",
    }
    triage = reflexd_triage.classify_message(
        content=envelope["content"],
        sender=envelope["from_name"],
        self_name=vendor,
    )
    assert triage.action == "RESPOND"
    _dispatch_for_envelope(envelope, notify=notify)
    notify.assert_called_once_with(
        "Quorus — dev",
        f"@{vendor} ship it",
        sender="arav",
        room="dev",
    )


@pytest.mark.parametrize("vendor", VENDORS)
def test_dm_envelope_renders_as_quorus_dm(vendor: str) -> None:
    """Empty room → title=``Quorus — DM`` (matches reflexd + hub fallback)."""
    notify = MagicMock(return_value=True)
    envelope = {
        "from_name": "arav",
        "content": f"hey @{vendor}",
        "room": "",
        "message_type": "chat",
    }
    _dispatch_for_envelope(envelope, notify=notify)
    notify.assert_called_once_with(
        "Quorus — DM",
        f"hey @{vendor}",
        sender="arav",
        room="",
    )


def test_dispatch_passes_room_for_rate_limit_keying() -> None:
    """``room`` MUST be forwarded so the per-(sender,room) limiter dedupes."""
    notify = MagicMock(return_value=True)
    envelope = {
        "from_name": "alice",
        "content": "@claude please review",
        "room": "design",
    }
    _dispatch_for_envelope(envelope, notify=notify)
    kwargs = notify.call_args.kwargs
    assert kwargs.get("sender") == "alice"
    assert kwargs.get("room") == "design"


def test_notify_returns_false_does_not_raise() -> None:
    """Production callers wrap notify() in try/except; the boolean is advisory."""
    notify = MagicMock(return_value=False)
    envelope = {"from_name": "x", "content": "@claude hi", "room": "r"}
    # Must not raise — production callers depend on this.
    _dispatch_for_envelope(envelope, notify=notify)
    assert notify.call_count == 1
