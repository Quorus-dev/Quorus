"""Verify presence list_all is cached per tenant and invalidated on heartbeat."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from quorus.backends.redis_backends import RedisPresenceBackend


@pytest.fixture
async def fake_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_list_all_caches_within_ttl(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("QUORUS_PRESENCE_CACHE_TTL", "5")
    backend = RedisPresenceBackend(fake_redis)
    await backend.heartbeat("tA", "alice", "online", "room1")
    await backend.heartbeat("tA", "bob", "online", "room1")

    first = await backend.list_all("tA", timeout_seconds=60)
    assert len(first) == 2

    # Mutate Redis directly behind the cache's back. Second call within
    # the TTL must NOT re-read, so the new member stays invisible.
    await fake_redis.zadd("t:tA:presence:_idx", {"carol": 1.0})
    await fake_redis.hset(
        "t:tA:presence:carol",
        mapping={
            "name": "carol", "status": "online", "room": "room1",
            "last_heartbeat": "2026-01-01T00:00:00+00:00",
            "uptime_start": "2026-01-01T00:00:00+00:00",
            "tenant_id": "tA",
        },
    )
    cached = await backend.list_all("tA", timeout_seconds=60)
    assert len(cached) == 2, "second call within TTL should be served from cache"


@pytest.mark.asyncio
async def test_heartbeat_invalidates_cache(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("QUORUS_PRESENCE_CACHE_TTL", "5")
    backend = RedisPresenceBackend(fake_redis)
    await backend.heartbeat("tA", "alice", "online", "room1")

    first = await backend.list_all("tA", timeout_seconds=60)
    assert len(first) == 1

    # New heartbeat invalidates the cache → next read is fresh.
    await backend.heartbeat("tA", "bob", "online", "room1")
    fresh = await backend.list_all("tA", timeout_seconds=60)
    assert len(fresh) == 2


@pytest.mark.asyncio
async def test_cache_disabled_when_ttl_zero(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("QUORUS_PRESENCE_CACHE_TTL", "0")
    backend = RedisPresenceBackend(fake_redis)
    await backend.heartbeat("tA", "alice", "online", "room1")

    await backend.list_all("tA", timeout_seconds=60)

    # Mutate behind the back; with TTL=0 the next call must re-read.
    await fake_redis.zadd("t:tA:presence:_idx", {"bob": 1.0})
    await fake_redis.hset(
        "t:tA:presence:bob",
        mapping={
            "name": "bob", "status": "online", "room": "room1",
            "last_heartbeat": "2026-01-01T00:00:00+00:00",
            "uptime_start": "2026-01-01T00:00:00+00:00",
            "tenant_id": "tA",
        },
    )
    second = await backend.list_all("tA", timeout_seconds=60)
    assert len(second) == 2, "ttl=0 must disable cache"


@pytest.mark.asyncio
async def test_cache_keyed_by_tenant(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("QUORUS_PRESENCE_CACHE_TTL", "60")
    backend = RedisPresenceBackend(fake_redis)
    await backend.heartbeat("tA", "alice", "online", "room1")
    await backend.heartbeat("tB", "bob", "online", "room1")

    a_first = await backend.list_all("tA", timeout_seconds=60)
    assert len(a_first) == 1

    # Tenant B's first call must still hit Redis (different cache key).
    b_first = await backend.list_all("tB", timeout_seconds=60)
    assert len(b_first) == 1
    assert b_first[0]["name"] == "bob"
