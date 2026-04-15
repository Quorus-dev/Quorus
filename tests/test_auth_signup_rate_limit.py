"""Tests for the per-IP signup rate limit (5 requests / 60 seconds).

Drives ``RateLimitService.check_with_limit`` directly with the in-memory
backend so we don't need a Postgres or Redis dependency.
"""
from __future__ import annotations

import inspect

import pytest

from quorus.auth import routes as auth_routes
from quorus.backends.memory import InMemoryRateLimitBackend
from quorus.services.rate_limit_svc import RateLimitService


@pytest.fixture
def rate_limit_service() -> RateLimitService:
    return RateLimitService(InMemoryRateLimitBackend(), window=60, max_count=60)


@pytest.mark.asyncio
async def test_signup_rate_limit_allows_first_five_then_blocks(rate_limit_service):
    ip = "203.0.113.99"
    key = f"signup:{ip}"
    for i in range(5):
        allowed = await rate_limit_service.check_with_limit(
            "global", key, max_count=5, window=60,
        )
        assert allowed, f"attempt {i + 1} should have been allowed"

    sixth = await rate_limit_service.check_with_limit(
        "global", key, max_count=5, window=60,
    )
    assert sixth is False, "6th signup in the same window must be rate-limited"


@pytest.mark.asyncio
async def test_signup_rate_limit_isolated_per_ip(rate_limit_service):
    for _ in range(5):
        await rate_limit_service.check_with_limit(
            "global", "signup:198.51.100.1", max_count=5, window=60,
        )
    other_ip = await rate_limit_service.check_with_limit(
        "global", "signup:198.51.100.2", max_count=5, window=60,
    )
    assert other_ip is True


def test_signup_handler_returns_json_with_retry_after_on_limit():
    """The handler must emit a 429 JSONResponse with a ``Retry-After``
    header when the limiter rejects. Verified via source inspection so we
    don't need the DB path."""
    source = inspect.getsource(auth_routes.signup)
    # JSONResponse is imported and used, not HTTPException (which wouldn't
    # give us fine-grained header control).
    assert "JSONResponse" in source
    assert "429" in source or "status_code=429" in source
    assert "Retry-After" in source
    assert "rate_limited" in source


def test_signup_window_is_sixty_seconds_not_an_hour():
    """Regression: the original code used window=3600 (5/hour) which is
    too permissive for a signup endpoint open to the internet."""
    source = inspect.getsource(auth_routes.signup)
    # Accept either a named constant equal to 60 or the literal.
    assert (
        "_SIGNUP_WINDOW = 60" in source
        or "window=60" in source
        or "window=_SIGNUP_WINDOW" in source
    ), "signup must rate-limit on a 60-second window"
    # And must NOT still use the old hour window.
    assert "window=3600" not in source, (
        "stale 5-per-hour signup limit was not removed"
    )
