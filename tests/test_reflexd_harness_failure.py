"""Raised adapter failures must leave a visible, safely worded room status."""

import asyncio

import pytest

from tests.test_reflexd_inbox_drain import (
    SELF,
    _D7Relay,
    _make_daemon,
    _wake_envelope,
)


@pytest.mark.parametrize(
    "error, reason",
    [(RuntimeError("private-token"), "crashed (RuntimeError)"),
     (TimeoutError("private-token"), "timed out")],
)
def test_raised_harness_failure_posts_status(monkeypatch, error, reason):
    daemon = _make_daemon()
    relay = _D7Relay(history=[])

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(daemon.adapter, "run", fail)
    asyncio.run(daemon._wake_and_reply(relay, _wake_envelope(), reason="test"))

    assert len(relay.posted) == 1
    posted = relay.posted[0]
    assert reason in posted["content"]
    assert "claude" in posted["content"]
    assert "Mention me to retry" in posted["content"]
    assert "private-token" not in posted["content"]
    assert posted["reply_to"] == "wake-msg-1"
    assert daemon._presence == ("active", "")


def test_raised_failure_is_suppressed_after_agent_reply(monkeypatch):
    daemon = _make_daemon()
    relay = _D7Relay(history=[
        {"from_name": SELF, "reply_to": "wake-msg-1", "content": "done"},
    ])

    async def fail(*args, **kwargs):
        raise RuntimeError("transport failed after posting")

    monkeypatch.setattr(daemon.adapter, "run", fail)
    asyncio.run(daemon._wake_and_reply(relay, _wake_envelope(), reason="test"))

    assert relay.posted == []


def test_cancelled_wake_does_not_post_failure(monkeypatch):
    daemon = _make_daemon()
    relay = _D7Relay(history=[])

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(daemon.adapter, "run", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(daemon._wake_and_reply(relay, _wake_envelope(), reason="test"))

    assert relay.posted == []
    assert daemon._presence == ("active", "")
