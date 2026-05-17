"""Regression test for the 2026-05-16 prod incident.

Symptom: Upstash Redis hit its 500K request quota; every real command failed
with ``ResponseError: max requests limit exceeded``; ``POST /rooms`` 500'd;
but ``/health`` happily reported ``{"redis": "connected"}`` because the old
health check used ``PING``, which Upstash exempts from the request quota.

Lesson: ``PING`` is not a sufficient health check on quota-metered Redis
providers. The new check uses ``SET ... EX 5`` which counts against quota
the same way real traffic does — so quota exhaustion now flips ``/health``
to 503 within one cache window (default 30s).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from quorus.backends import redis_client


@pytest.fixture(autouse=True)
def reset_cache():
    """Make every test see a cold cache."""
    redis_client._health_cache = {"ok": False, "checked_at": 0.0}
    yield
    redis_client._health_cache = {"ok": False, "checked_at": 0.0}


@pytest.mark.asyncio
async def test_health_check_uses_set_not_ping():
    """The fix REQUIRES a real round-trip — SET — not PING.

    If a future PR ever swaps this back to ``await _redis.ping()``, this
    test fails. The Upstash quota-exhaustion incident proves PING is
    insufficient.
    """
    import inspect

    src = inspect.getsource(redis_client.check_redis)
    assert "_redis.set(" in src, (
        "check_redis must exercise SET — not PING — so that quota-metered "
        "Redis providers (Upstash, Vercel KV-via-Upstash, etc.) accurately "
        "reflect command-failure conditions in /health."
    )


@pytest.mark.asyncio
async def test_set_failure_marks_unhealthy():
    """When SET raises (quota exhausted, network blip, auth fail), the
    health check must return False — not silently swallow."""
    fake_redis = AsyncMock()
    fake_redis.set.side_effect = Exception(
        "max requests limit exceeded. Limit: 500000, Usage: 500000."
    )
    with patch.object(redis_client, "_redis", fake_redis):
        result = await redis_client.check_redis()
    assert result is False, (
        "quota-exhausted Redis must mark /health unhealthy; the cached "
        "value should be False so the next /health returns 503"
    )
    assert redis_client._health_cache["ok"] is False


@pytest.mark.asyncio
async def test_set_success_marks_healthy():
    """Happy path — SET succeeds, healthy."""
    fake_redis = AsyncMock()
    fake_redis.set.return_value = True
    with patch.object(redis_client, "_redis", fake_redis):
        result = await redis_client.check_redis()
    assert result is True
    # And the SET we did had the right shape — short TTL key
    fake_redis.set.assert_called_once()
    args, kwargs = fake_redis.set.call_args
    assert args[0] == "__quorus_health__"
    assert "ex" in kwargs and kwargs["ex"] <= 60, (
        "health-check SET must have a short TTL so the key self-evicts and "
        "doesn't bloat Redis memory"
    )


@pytest.mark.asyncio
async def test_no_redis_returns_false():
    """If init_redis() was never called, check_redis must return False
    (and not raise)."""
    with patch.object(redis_client, "_redis", None):
        result = await redis_client.check_redis()
    assert result is False
