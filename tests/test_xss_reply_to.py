"""Regression tests for the dashboard reply_to XSS (2026-05-16 audit).

Two layers of defense are tested here:

1. ``RoomMessageRequest`` MUST reject any ``reply_to`` / ``brief_id`` /
   ``thread_root_id`` that is not a UUID. This closes the attack at the
   API boundary — an attacker cannot land a payload like
   ``'"><script>...</script>`` into stored state in the first place.

2. ``quorus/dashboard.py`` MUST NOT inline-interpolate user-supplied IDs
   into JS string contexts (``onclick="scrollToMsg('...')"``). The
   replacement uses ``data-*`` attributes + event delegation, so even if
   layer 1 regresses, the JS context is no longer reachable.
"""

from __future__ import annotations

import re
import uuid

import pytest

from quorus.routes.models import RoomMessageRequest


GOOD_UUID = str(uuid.uuid4())


def test_reply_to_accepts_uuid() -> None:
    m = RoomMessageRequest(from_name="alice", content="hi", reply_to=GOOD_UUID)
    assert m.reply_to == GOOD_UUID


def test_reply_to_accepts_none() -> None:
    m = RoomMessageRequest(from_name="alice", content="hi")
    assert m.reply_to is None


def test_reply_to_treats_empty_as_none() -> None:
    m = RoomMessageRequest(from_name="alice", content="hi", reply_to="")
    assert m.reply_to is None


@pytest.mark.parametrize(
    "payload",
    [
        '"><script>alert(1)</script>',
        "'); evil(); //",
        "../../etc/passwd",
        "not-a-uuid",
        "<img src=x onerror=alert(1)>",
        "javascript:alert(1)",
        # UUID-looking but with extra char — must still be rejected
        f"{GOOD_UUID}-extra",
        # UUID-looking but malformed
        "00000000-0000-0000-0000-00000000000Z",
    ],
)
def test_reply_to_rejects_xss_payloads(payload: str) -> None:
    with pytest.raises(ValueError):
        RoomMessageRequest(from_name="alice", content="hi", reply_to=payload)


def test_thread_root_id_also_validated() -> None:
    """``thread_root_id`` is also rendered server-side eventually — must reject XSS."""
    with pytest.raises(ValueError):
        RoomMessageRequest(
            from_name="alice", content="hi", thread_root_id='"><script>alert(1)</script>'
        )


def test_brief_id_remains_opaque() -> None:
    """``brief_id`` is intentionally NOT UUID-validated — it's an opaque CLI ref
    not rendered in any user-facing HTML surface. If a future PR ever renders
    it without escaping, add validation here AND a dashboard XSS test."""
    m = RoomMessageRequest(from_name="alice", content="hi", brief_id="test-brief-uuid")
    assert m.brief_id == "test-brief-uuid"


def test_dashboard_no_inline_onclick_with_user_ids() -> None:
    """The reply-to rendering paths must not interpolate IDs into JS strings.

    We assert that the message-rendering helper uses ``data-*`` attributes
    rather than inline ``onclick="scrollToMsg('"+msg.reply_to+"')"``.
    """
    from quorus.dashboard import DASHBOARD_HTML

    # The fixed version uses dataset-based delegation. If anyone ever puts
    # ``onclick="scrollToMsg('"`` back, this test should fail.
    bad_patterns = [
        re.compile(r"onclick=\"scrollToMsg\('\"\+\s*msg\.reply_to"),
        re.compile(r"onclick=\"setReply\('\"\+\s*mid\s*\+"),
    ]
    for pat in bad_patterns:
        assert pat.search(DASHBOARD_HTML) is None, (
            f"dashboard regressed to inline-onclick pattern: {pat.pattern}"
        )

    # And assert the data-* attributes the fix introduces are present.
    assert "data-scroll-to=" in DASHBOARD_HTML
    assert "data-reply-id=" in DASHBOARD_HTML
