"""Tests for SSE circuit breaker (SSEListener)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from quorus_mcp.sse import _FAILURE_THRESHOLD, SSEListener


def _make_listener(on_event=None):
    """Build a minimal SSEListener with mock dependencies."""
    async def get_token():
        return "test-token"

    async def noop_event(event_type, data):
        pass

    return SSEListener(
        relay_url="http://relay:8080",
        instance_name="test-agent",
        get_http_client=lambda: None,
        get_sse_token=get_token,
        on_event=on_event or noop_event,
    )


@pytest.mark.asyncio
async def test_breaker_trips_after_threshold_failures():
    """After _FAILURE_THRESHOLD consecutive failures, breaker trips."""
    listener = _make_listener()

    async def instant_sleep(stop_event, duration):
        pass

    listener._sleep_or_stop = instant_sleep
    stop_event = asyncio.Event()
    call_count = 0

    async def attempt_and_stop(se, probe):
        nonlocal call_count
        call_count += 1
        await listener._record_failure(f"HTTP 503 (call {call_count})")
        if call_count >= _FAILURE_THRESHOLD:
            stop_event.set()
        return False

    listener._attempt_connect = attempt_and_stop
    await listener.run(stop_event)

    assert listener._tripped is True
    assert listener._failures >= _FAILURE_THRESHOLD
    assert listener._last_error is not None


@pytest.mark.asyncio
async def test_breaker_clears_on_successful_probe():
    """Breaker resets when a probe reconnect succeeds."""
    listener = _make_listener()
    listener._failures = _FAILURE_THRESHOLD
    listener._tripped = True
    listener._last_error = "HTTP 503"

    stop_event = asyncio.Event()

    async def mock_attempt(se, probe):
        stop_event.set()
        return True

    listener._attempt_connect = mock_attempt

    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        await listener.run(stop_event)

    assert listener._tripped is False
    assert listener._failures == 0
    assert listener._last_error is None


@pytest.mark.asyncio
async def test_breaker_state_returns_dict():
    """breaker_state() returns a properly shaped dict."""
    listener = _make_listener()
    state = listener.breaker_state()
    assert set(state.keys()) == {"tripped", "failures", "last_error"}
    assert state["tripped"] is False
    assert state["failures"] == 0
    assert state["last_error"] is None


@pytest.mark.asyncio
async def test_record_failure_increments_counter():
    """_record_failure increments failures and sets last_error."""
    listener = _make_listener()
    await listener._record_failure("some error")
    assert listener._failures == 1
    assert listener._last_error == "some error"
    assert listener._tripped is False


@pytest.mark.asyncio
async def test_record_failure_exactly_at_threshold_trips():
    """Breaker trips exactly at _FAILURE_THRESHOLD failures."""
    listener = _make_listener()
    for i in range(_FAILURE_THRESHOLD - 1):
        await listener._record_failure(f"error {i}")
    assert listener._tripped is False

    await listener._record_failure("final error")
    assert listener._tripped is True


# ---------------------------------------------------------------------------
# Server-level integration: _sse_breaker_state() + polling-fallback gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_breaker_state_default_when_no_listener():
    """Without an active listener, _sse_breaker_state returns the cleared shape."""
    import quorus.mcp_server as mcp_server

    mcp_server._reset_runtime_state()
    state = mcp_server._sse_breaker_state()
    assert state == {"tripped": False, "failures": 0, "last_error": None}


@pytest.mark.asyncio
async def test_server_breaker_state_reflects_listener_after_failures():
    """Once a listener is bound, _sse_breaker_state surfaces its counters."""
    import quorus.mcp_server as mcp_server

    mcp_server._reset_runtime_state()
    listener = _make_listener()
    mcp_server._active_sse_listener = listener
    try:
        for i in range(_FAILURE_THRESHOLD):
            await listener._record_failure(f"err {i}")
        state = mcp_server._sse_breaker_state()
        assert state["tripped"] is True
        assert state["failures"] >= _FAILURE_THRESHOLD
        assert state["last_error"] is not None
    finally:
        mcp_server._active_sse_listener = None
        mcp_server._reset_runtime_state()


@pytest.mark.asyncio
async def test_server_breaker_state_clears_after_probe_success():
    """A reset listener clears the breaker view at the server level."""
    import quorus.mcp_server as mcp_server

    mcp_server._reset_runtime_state()
    listener = _make_listener()
    mcp_server._active_sse_listener = listener
    try:
        for i in range(_FAILURE_THRESHOLD):
            await listener._record_failure(f"err {i}")
        assert mcp_server._sse_breaker_state()["tripped"] is True

        # Simulate a successful probe -> SSEListener.run() does this under
        # _breaker_lock; mirror that here.
        async with listener._breaker_lock:
            listener._tripped = False
            listener._failures = 0
            listener._last_error = None

        state = mcp_server._sse_breaker_state()
        assert state == {"tripped": False, "failures": 0, "last_error": None}
    finally:
        mcp_server._active_sse_listener = None
        mcp_server._reset_runtime_state()


@pytest.mark.asyncio
async def test_polling_fallback_skips_when_breaker_clear():
    """Fallback must NOT call _fetch_relay_messages while the breaker is clear."""
    import quorus.mcp_server as mcp_server

    mcp_server._reset_runtime_state()
    calls: list[int] = []

    async def fake_fetch(wait):
        calls.append(wait)
        return ([], None, None)

    stop = asyncio.Event()
    stop.set()  # exit immediately on the first sleep

    with patch.object(mcp_server, "_fetch_relay_messages", fake_fetch):
        await mcp_server._polling_fallback(stop)

    assert calls == []
    mcp_server._reset_runtime_state()


@pytest.mark.asyncio
async def test_polling_fallback_runs_when_breaker_tripped():
    """When the breaker is tripped, fallback must drain the relay."""
    import quorus.mcp_server as mcp_server

    mcp_server._reset_runtime_state()
    listener = _make_listener()
    for i in range(_FAILURE_THRESHOLD):
        await listener._record_failure(f"err {i}")
    mcp_server._active_sse_listener = listener
    assert mcp_server._sse_breaker_state()["tripped"] is True

    fetched: list[dict] = []

    async def fake_fetch(wait):
        # Stop after one drain so the test is bounded.
        stop_event.set()
        msg = {"id": "m1", "from_name": "x", "content": "y", "timestamp": "t"}
        return ([msg], "ack-token-1", None)

    acked: list[str] = []

    async def fake_ack(token):
        acked.append(token)

    appended: list[list[dict]] = []

    async def fake_append(messages):
        appended.append(list(messages))

    stop_event = asyncio.Event()
    try:
        with patch.object(mcp_server, "_fetch_relay_messages", fake_fetch), \
             patch.object(mcp_server, "_ack_messages", fake_ack), \
             patch.object(mcp_server, "_append_pending_messages", fake_append), \
             patch.object(mcp_server, "_notify_active_session",
                          lambda _msgs: asyncio.sleep(0)):
            await mcp_server._polling_fallback(stop_event)
    finally:
        mcp_server._active_sse_listener = None
        mcp_server._reset_runtime_state()

    assert appended and appended[0][0]["id"] == "m1"
    assert acked == ["ack-token-1"]
    fetched.append({"ok": True})
    assert fetched
