"""Redis connection management for Quorus relay backends.

Usage::

    from quorus.backends.redis_client import init_redis, get_redis, close_redis

    await init_redis()           # call once at startup
    r = get_redis()              # fast, synchronous accessor
    await close_redis()          # call on shutdown
"""

from __future__ import annotations

import os
import time

from redis.asyncio import Redis

_redis: Redis | None = None

_DEFAULT_URL = "redis://localhost:6379/0"

# Health check cache to reduce PING commands
_health_cache: dict[str, float] = {"ok": False, "checked_at": 0.0}
_HEALTH_CACHE_TTL = 30.0  # Cache health status for 30 seconds


async def init_redis(url: str | None = None) -> Redis:
    """Create (or return the existing) async Redis connection."""
    global _redis  # noqa: PLW0603
    if _redis is not None:
        return _redis
    resolved_url = url or os.environ.get("REDIS_URL", _DEFAULT_URL)
    _redis = Redis.from_url(resolved_url, decode_responses=True)
    # Verify connectivity
    await _redis.ping()
    return _redis


async def close_redis() -> None:
    """Gracefully close the Redis connection pool."""
    global _redis  # noqa: PLW0603
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def get_redis() -> Redis:
    """Return the current Redis connection or raise if not initialised."""
    if _redis is None:
        raise RuntimeError(
            "Redis not initialised. Call await init_redis() first."
        )
    return _redis


async def check_redis() -> bool:
    """Return ``True`` if the Redis connection is healthy.

    Caches the result for _HEALTH_CACHE_TTL seconds to reduce PING commands.
    """
    global _health_cache
    now = time.monotonic()

    # Return cached result if still fresh
    if now - _health_cache["checked_at"] < _HEALTH_CACHE_TTL:
        return bool(_health_cache["ok"])

    if _redis is None:
        _health_cache = {"ok": False, "checked_at": now}
        return False
    try:
        await _redis.ping()
        _health_cache = {"ok": True, "checked_at": now}
        return True
    except Exception:
        _health_cache = {"ok": False, "checked_at": now}
        return False
