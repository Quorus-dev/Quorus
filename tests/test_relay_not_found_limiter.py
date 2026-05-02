"""Tests for the 404 spam rate limiter (relay.py).

Asserts that:

  1. The Redis-backed path is preferred when a ``rate_limit_service`` is
     attached to ``app.state`` and ``REDIS_URL`` is configured. Verified
     via source inspection so the test does not require a running Redis.
  2. The in-memory fallback (``_record_not_found_memory`` /
     ``_is_blocked_memory``) blocks an IP after ``NOT_FOUND_LIMIT``
     consecutive 404s and unblocks once the window expires.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

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
