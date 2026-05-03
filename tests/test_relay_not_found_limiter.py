"""Tests for the 404 spam rate limiter (relay.py).

Asserts that:

  1. The Redis-backed path is preferred when a ``rate_limit_service`` is
     attached to ``app.state`` and ``REDIS_URL`` is configured. Verified
     via source inspection so the test does not require a running Redis.
  2. The in-memory fallback (``_record_not_found_memory`` /
     ``_is_blocked_memory``) blocks an IP after ``NOT_FOUND_LIMIT``
     consecutive 404s and unblocks once the window expires.
  3. The middleware fails OPEN when Redis raises (e.g. Upstash quota
     exhaustion). On 2026-05-03 the Upstash request quota was exhausted
     and the middleware re-raised ``ResponseError``, taking the entire
     relay down with 500s on every request. Anti-abuse must never be a
     single point of failure for the whole service.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.responses import JSONResponse

from quorus import relay


def test_middleware_uses_rate_limit_service_when_redis_configured():
    """The middleware must check ``rate_limit_service`` and the Redis URL
    before falling back to the per-replica in-memory limiter."""
    source = inspect.getsource(relay.NotFoundRateLimitMiddleware)
    # Redis path is gated on the service + REDIS_URL.
    assert "rate_limit_service" in source, (
        "404 limiter must reach for the shared rate-limit service first"
    )
    assert "REDIS_URL" in source, (
        "Redis path must be guarded by REDIS_URL so unconfigured deployments "
        "still get the in-memory fallback"
    )
    assert "_record_not_found_memory" in source or "use_redis" in source, (
        "in-memory fallback must remain reachable"
    )


def test_in_memory_fallback_blocks_after_limit(monkeypatch):
    """Direct unit test on the in-memory helpers."""
    # Reset shared per-replica state to keep the test isolated.
    relay._not_found_counts.clear()
    relay._blocked_ips.clear()

    # Tighten the limit so the test stays fast.
    monkeypatch.setattr(relay, "NOT_FOUND_LIMIT", 3)
    monkeypatch.setattr(relay, "NOT_FOUND_WINDOW", 60)
    monkeypatch.setattr(relay, "NOT_FOUND_BLOCK_DURATION", 30)

    ip = "198.51.100.42"

    async def _run() -> None:
        # First N-1 hits should not block.
        for i in range(relay.NOT_FOUND_LIMIT - 1):
            blocked_now = await relay._record_not_found_memory(ip)
            assert blocked_now is False, f"hit #{i + 1} should not trip block"
            blocked, _ = await relay._is_blocked_memory(ip)
            assert blocked is False

        # Nth hit must trip the block.
        tripped = await relay._record_not_found_memory(ip)
        assert tripped is True

        blocked, retry_after = await relay._is_blocked_memory(ip)
        assert blocked is True
        assert retry_after > 0

    asyncio.run(_run())

    # Cleanup so other tests are unaffected.
    relay._not_found_counts.clear()
    relay._blocked_ips.clear()


def test_in_memory_fallback_unblocks_after_expiry(monkeypatch):
    """Once the block expiry has passed, ``_is_blocked_memory`` returns False
    and removes the IP from the blocklist."""
    relay._not_found_counts.clear()
    relay._blocked_ips.clear()

    ip = "198.51.100.43"
    # Set an expiry already in the past.
    relay._blocked_ips[ip] = 0.0

    async def _run() -> None:
        blocked, retry_after = await relay._is_blocked_memory(ip)
        assert blocked is False
        assert retry_after == 0
        assert ip not in relay._blocked_ips

    asyncio.run(_run())


def test_exempt_paths_skip_the_limiter():
    """Health and metrics endpoints must not be rate limited, otherwise a
    misbehaving probe could lock itself out."""
    assert "/health" in relay._NOT_FOUND_EXEMPT_PATHS
    assert "/metrics" in relay._NOT_FOUND_EXEMPT_PATHS


def test_middleware_fails_open_on_redis_error(monkeypatch):
    """If Redis raises (quota exhausted, network blip, anything) the
    middleware MUST log + fall back to the in-memory limiter and let the
    request through. The bug we are guarding against: on 2026-05-03 the
    Upstash quota was exhausted and ResponseError propagated, returning
    500 on every relay request including /v1/auth/token.
    """
    relay._not_found_counts.clear()
    relay._blocked_ips.clear()
    monkeypatch.setattr(relay, "REDIS_URL", "redis://stub")

    # Build a fake rate_limit_service that raises like Upstash does.
    class _StubResponseError(Exception):
        pass

    failing_svc = MagicMock()
    failing_svc.is_rate_limited = AsyncMock(
        side_effect=_StubResponseError(
            "max requests limit exceeded. Limit: 500000, Usage: 500000"
        )
    )
    failing_svc.record = AsyncMock(side_effect=_StubResponseError("quota"))

    # Fake Request with state.app.state.rate_limit_service set.
    fake_app = MagicMock()
    fake_app.state.rate_limit_service = failing_svc

    fake_request = MagicMock()
    fake_request.url.path = "/rooms"  # NOT in the exempt set
    fake_request.app = fake_app
    fake_request.headers = {"x-forwarded-for": "203.0.113.99"}
    fake_request.client = None

    # call_next returns a 200 response to keep the test focused on the
    # rate-limit branch; we just need the middleware to not raise.
    sentinel_response = JSONResponse(status_code=200, content={"ok": True})
    call_next = AsyncMock(return_value=sentinel_response)

    middleware = relay.NotFoundRateLimitMiddleware(app=lambda *a, **kw: None)

    async def _run():
        return await middleware.dispatch(fake_request, call_next)

    response = asyncio.run(_run())
    assert response is sentinel_response, (
        "middleware must return the call_next response after failing open"
    )
    failing_svc.is_rate_limited.assert_awaited_once()
    call_next.assert_awaited_once_with(fake_request)


def test_middleware_fails_open_on_record_redis_error(monkeypatch):
    """The 404-record path also runs Redis. If that errors, the response
    must still be returned to the client — we just lose anti-abuse for
    that one request."""
    relay._not_found_counts.clear()
    relay._blocked_ips.clear()
    monkeypatch.setattr(relay, "REDIS_URL", "redis://stub")

    failing_svc = MagicMock()
    failing_svc.is_rate_limited = AsyncMock(return_value=False)  # not blocked
    failing_svc.record = AsyncMock(
        side_effect=Exception("max requests limit exceeded")
    )

    fake_app = MagicMock()
    fake_app.state.rate_limit_service = failing_svc

    fake_request = MagicMock()
    fake_request.url.path = "/rooms/does-not-exist"
    fake_request.app = fake_app
    fake_request.headers = {"x-forwarded-for": "203.0.113.100"}
    fake_request.client = None

    not_found = JSONResponse(status_code=404, content={"detail": "nope"})
    call_next = AsyncMock(return_value=not_found)

    middleware = relay.NotFoundRateLimitMiddleware(app=lambda *a, **kw: None)

    async def _run():
        return await middleware.dispatch(fake_request, call_next)

    response = asyncio.run(_run())
    assert response is not_found, (
        "client must still receive the 404 even when Redis record() fails"
    )


@pytest.mark.parametrize(
    "header_value, expected",
    [
        ("203.0.113.1", "203.0.113.1"),
        ("203.0.113.1, 198.51.100.99", "203.0.113.1"),
        ("  203.0.113.5  ", "203.0.113.5"),
    ],
)
def test_get_client_ip_handles_xff(header_value, expected):
    """X-Forwarded-For parsing must trim whitespace and pick the first hop."""

    class _StubRequest:
        def __init__(self, xff: str) -> None:
            self.headers = {"x-forwarded-for": xff}
            self.client = None

    assert relay._get_client_ip(_StubRequest(header_value)) == expected
