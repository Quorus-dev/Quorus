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


@pytest.mark.parametrize("exit_code", [1, 17, -9, 0])
@pytest.mark.parametrize("self_posted", [False, True])
def test_unknown_subprocess_exit_status_after_self_post_check(
    monkeypatch, exit_code, self_posted,
):
    """Exercise the subprocess, adapter and room paths without exposing stderr."""
    import json

    from tests.test_reflexd_adapters import _FakeProc
    from tests.test_reflexd_inbox_drain import reflexd

    daemon = _make_daemon()
    history = ([{"from_name": SELF, "reply_to": "wake-msg-1", "content": "done"}]
               if self_posted else [])
    relay = _D7Relay(history=history)

    async def spawn(*args, **kwargs):
        return _FakeProc(
            returncode=exit_code, stderr=b"private-token: unexplained error",
            stdout=json.dumps({"type": "result", "is_error": True,
                               "result": "private-token: unexplained error"}).encode(),
        )

    monkeypatch.setattr(reflexd.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(reflexd.shutil, "which", lambda name: "/bin/harness")
    daemon.adapter = reflexd.HeadlessAdapter()
    asyncio.run(daemon._wake_and_reply(relay, _wake_envelope(), reason="test"))

    if self_posted:
        assert relay.posted == []
    else:
        assert len(relay.posted) == 1
        assert relay.posted[0]["content"] == (
            f"⚠ claude exited {exit_code} with no known cause; mention me to retry"
        )
        assert relay.posted[0]["reply_to"] == "wake-msg-1"
    assert daemon._presence == ("active", "")
