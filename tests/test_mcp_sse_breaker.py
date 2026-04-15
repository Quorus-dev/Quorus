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
