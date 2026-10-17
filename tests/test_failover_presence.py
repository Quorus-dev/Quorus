"""Both presence backends retain last-wake ordering and degraded reasons."""

import fakeredis.aioredis
import pytest

from quorus.backends.memory import InMemoryPresenceBackend
from quorus.backends.redis_backends import RedisPresenceBackend


@pytest.mark.parametrize("redis", [False, True])
async def test_wake_timestamp_and_degraded_reason(redis):
    connection = fakeredis.aioredis.FakeRedis(decode_responses=True)
    backend = RedisPresenceBackend(connection) if redis else InMemoryPresenceBackend()
    try:
        busy = await backend.heartbeat("t", "alice", "busy", "r")
        timestamp = busy["last_wake_at"]
        again = await backend.heartbeat("t", "alice", "busy", "r")
        assert again["last_wake_at"] == timestamp
        await backend.heartbeat("t", "alice", "degraded", "r", "quota")
        entry = (await backend.list_all("t", 90))[0]
        assert entry["reason"] == "quota" and entry["status"] == "degraded"
        assert entry["last_wake_at"] == timestamp
        active = await backend.heartbeat("t", "alice", "active", "")
        assert active["last_wake_at"] == timestamp and active["reason"] == ""
        assert (await backend.heartbeat("t", "alice", "busy", "r"))["last_wake_at"]
    finally:
        await connection.aclose()
