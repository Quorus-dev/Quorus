"""Redis-backed implementations of all Murmur backend protocols.

Each class wraps a ``redis.asyncio.Redis`` instance and stores data
using the key schema documented in the production architecture spec.
All keys are tenant-scoped with the prefix ``t:{tenant_id}:``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis

MESSAGE_TTL = int(os.environ.get("MESSAGE_TTL_SECONDS", "86400"))
VISIBILITY_TIMEOUT = int(os.environ.get("VISIBILITY_TIMEOUT_SECONDS", "60"))
SSE_TOKEN_TTL = int(os.environ.get("SSE_TOKEN_TTL", "300"))
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))

# Lua script for atomic sliding-window rate limiting
_RATE_LIMIT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_count = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= max_count then return 0 end
redis.call('ZADD', key, now, now .. ':' .. math.random(1000000))
redis.call('EXPIRE', key, window)
return 1
"""


# -- Messages (DM inboxes) -------------------------------------------------

class RedisMessageBackend:
    """Per-recipient DM inbox backed by Redis Lists."""

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _key(self, tid: str, name: str) -> str:
        return f"t:{tid}:dm:{name}"

    async def enqueue(self, tenant_id: str, to_name: str, message: dict) -> None:
        key = self._key(tenant_id, to_name)
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(message))
            pipe.expire(key, MESSAGE_TTL)
            await pipe.execute()

    async def enqueue_batch(
        self, tenant_id: str, to_name: str, messages: list[dict]
    ) -> None:
        if not messages:
            return
        key = self._key(tenant_id, to_name)
        encoded = [json.dumps(m) for m in messages]
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, *encoded)
            pipe.expire(key, MESSAGE_TTL)
            await pipe.execute()

    def _inflight_key(self, tid: str, name: str, token: str) -> str:
        return f"t:{tid}:dm:{name}:inflight:{token}"

    async def dequeue_all(self, tenant_id: str, to_name: str) -> list[dict]:
        key = self._key(tenant_id, to_name)
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            results = await pipe.execute()
        return [json.loads(m) for m in results[0]]

    # Lua script: atomically read all messages, move to inflight, set TTL.
    # Returns the messages that were moved — no race with concurrent enqueue.
    _FETCH_SCRIPT = """
    local inbox = KEYS[1]
    local inflight = KEYS[2]
    local ttl = tonumber(ARGV[1])
    local msgs = redis.call('LRANGE', inbox, 0, -1)
    if #msgs == 0 then return {} end
    redis.call('DEL', inbox)
    for i, m in ipairs(msgs) do
        redis.call('RPUSH', inflight, m)
    end
    redis.call('EXPIRE', inflight, ttl)
    return msgs
    """

    async def fetch(
        self, tenant_id: str, to_name: str
    ) -> tuple[list[dict], str]:
        key = self._key(tenant_id, to_name)
        token = uuid.uuid4().hex
        inflight = self._inflight_key(tenant_id, to_name, token)

        # Atomic Lua: read + delete inbox + copy to inflight in one call.
        msgs_raw = await self._r.eval(
            self._FETCH_SCRIPT, 2, key, inflight, VISIBILITY_TIMEOUT,
        )
        if not msgs_raw:
            return [], ""

        messages = [json.loads(m) for m in msgs_raw]
        return messages, token

    async def ack(
        self, tenant_id: str, to_name: str, ack_token: str
    ) -> None:
        if not ack_token:
            return
        inflight = self._inflight_key(tenant_id, to_name, ack_token)
        await self._r.delete(inflight)

    async def peek(self, tenant_id: str, to_name: str) -> int:
        return await self._r.llen(self._key(tenant_id, to_name))

    async def count_all(self, tenant_id: str) -> int:
        """Count total pending messages across all recipients for a tenant."""
        cursor, total = "0", 0
        while True:
            cursor, keys = await self._r.scan(
                cursor=cursor, match=f"t:{tenant_id}:dm:*", count=100
            )
            for key in keys:
                # Skip inflight keys (contain :inflight:)
                key_str = key if isinstance(key, str) else key.decode()
                if ":inflight:" in key_str:
                    continue
                total += await self._r.llen(key)
            if cursor == 0 or cursor == "0":
                break
        return total

    async def count_all_global(self) -> int:
        """Count total pending messages globally."""
        cursor, total = "0", 0
        while True:
            cursor, keys = await self._r.scan(
                cursor=cursor, match="t:*:dm:*", count=100
            )
            for key in keys:
                key_str = key if isinstance(key, str) else key.decode()
                if ":inflight:" in key_str:
                    continue
                total += await self._r.llen(key)
            if cursor == 0 or cursor == "0":
                break
        return total


# -- Rooms ------------------------------------------------------------------

class RedisRoomBackend:
    """Room CRUD with Redis Hashes and a room index Set."""

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _meta_key(self, tid: str, rid: str) -> str:
        return f"t:{tid}:room:{rid}:meta"

    def _members_key(self, tid: str, rid: str) -> str:
        return f"t:{tid}:room:{rid}:members"

    def _name_index_key(self, tid: str) -> str:
        return f"t:{tid}:room:names"

    def _room_index_key(self, tid: str) -> str:
        return f"t:{tid}:room:_index"

    async def create(self, tenant_id: str, room_id: str, room_data: dict) -> None:
        meta = {k: v for k, v in room_data.items() if k != "members"}
        members = room_data.get("members", {})
        async with self._r.pipeline(transaction=True) as pipe:
            if meta:
                pipe.hset(self._meta_key(tenant_id, room_id), mapping=meta)
            if members:
                pipe.hset(self._members_key(tenant_id, room_id), mapping=members)
            name = room_data.get("name")
            if name:
                pipe.hset(self._name_index_key(tenant_id), name, room_id)
            pipe.sadd(self._room_index_key(tenant_id), room_id)
            await pipe.execute()

    async def get(self, tenant_id: str, room_id: str) -> dict | None:
        meta = await self._r.hgetall(self._meta_key(tenant_id, room_id))
        if not meta:
            return None
        meta["members"] = await self._r.hgetall(self._members_key(tenant_id, room_id))
        return meta

    async def get_by_name(self, tenant_id: str, name: str) -> tuple[str, dict] | None:
        room_id = await self._r.hget(self._name_index_key(tenant_id), name)
        if room_id is None:
            return None
        data = await self.get(tenant_id, room_id)
        return (room_id, data) if data is not None else None

    async def list_all(self, tenant_id: str) -> list[tuple[str, dict]]:
        room_ids = await self._r.smembers(self._room_index_key(tenant_id))
        results: list[tuple[str, dict]] = []
        for rid in room_ids:
            data = await self.get(tenant_id, rid)
            if data is not None:
                results.append((rid, data))
        return results

    async def update(self, tenant_id: str, room_id: str, updates: dict) -> None:
        meta_key = self._meta_key(tenant_id, room_id)
        if not await self._r.exists(meta_key):
            return
        new_name = updates.get("name")
        if new_name is not None:
            old_name = await self._r.hget(meta_key, "name")
            if old_name and old_name != new_name:
                await self._r.hdel(self._name_index_key(tenant_id), old_name)
            if new_name:
                await self._r.hset(self._name_index_key(tenant_id), new_name, room_id)
        fields = {k: v for k, v in updates.items() if k != "members"}
        if fields:
            await self._r.hset(meta_key, mapping=fields)

    async def delete(self, tenant_id: str, room_id: str) -> None:
        meta_key = self._meta_key(tenant_id, room_id)
        name = await self._r.hget(meta_key, "name")
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.delete(meta_key)
            pipe.delete(self._members_key(tenant_id, room_id))
            if name:
                pipe.hdel(self._name_index_key(tenant_id), name)
            pipe.srem(self._room_index_key(tenant_id), room_id)
            await pipe.execute()

    async def add_member(
        self, tenant_id: str, room_id: str, name: str, role: str
    ) -> None:
        if not await self._r.exists(self._meta_key(tenant_id, room_id)):
            return
        await self._r.hset(self._members_key(tenant_id, room_id), name, role)

    async def remove_member(self, tenant_id: str, room_id: str, name: str) -> None:
        if not await self._r.exists(self._meta_key(tenant_id, room_id)):
            return
        await self._r.hdel(self._members_key(tenant_id, room_id), name)

    async def get_members(self, tenant_id: str, room_id: str) -> dict[str, str]:
        return await self._r.hgetall(self._members_key(tenant_id, room_id))

    async def count(self, tenant_id: str) -> int:
        """Count rooms in a tenant."""
        return await self._r.scard(self._room_index_key(tenant_id))

    async def count_global(self) -> int:
        """Count all rooms across all tenants."""
        cursor, total = "0", 0
        while True:
            cursor, keys = await self._r.scan(
                cursor=cursor, match="t:*:room:_index", count=100
            )
            for key in keys:
                total += await self._r.scard(key)
            if cursor == 0 or cursor == "0":
                break
        return total


# -- Room history -----------------------------------------------------------

class RedisRoomHistoryBackend:
    """Append-only room history backed by Redis Lists with LTRIM."""

    def __init__(self, r: Redis, max_history: int = 200) -> None:
        self._r = r
        self._max = max_history

    def _key(self, tid: str, rid: str) -> str:
        return f"t:{tid}:room:{rid}:history"

    async def append(self, tenant_id: str, room_id: str, message: dict) -> None:
        key = self._key(tenant_id, room_id)
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(message))
            pipe.ltrim(key, -self._max, -1)
            await pipe.execute()

    async def get_recent(self, tenant_id: str, room_id: str, limit: int) -> list[dict]:
        raw = await self._r.lrange(self._key(tenant_id, room_id), -limit, -1)
        return [json.loads(m) for m in raw]

    async def search(
        self, tenant_id: str, room_id: str,
        q: str = "", sender: str = "", message_type: str = "", limit: int = 50,
    ) -> list[dict]:
        raw = await self._r.lrange(self._key(tenant_id, room_id), 0, -1)
        results: list[dict] = []
        q_lower = q.lower()
        for item in reversed(raw):
            msg = json.loads(item)
            if q and q_lower not in msg.get("content", "").lower():
                continue
            if sender and msg.get("from_name", msg.get("from", "")) != sender:
                continue
            if message_type and msg.get("message_type", msg.get("type", "")) != message_type:
                continue
            results.append(msg)
            if len(results) >= limit:
                break
        results.reverse()
        return results

    async def delete(self, tenant_id: str, room_id: str) -> None:
        """Delete history for a room."""
        await self._r.delete(self._key(tenant_id, room_id))

    async def rename_room_in_history(
        self, tenant_id: str, room_id: str, new_name: str
    ) -> None:
        """Update room name in all history messages."""
        key = self._key(tenant_id, room_id)
        raw = await self._r.lrange(key, 0, -1)
        if not raw:
            return
        updated = []
        for item in raw:
            msg = json.loads(item)
            msg["room"] = new_name
            updated.append(json.dumps(msg))
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            if updated:
                pipe.rpush(key, *updated)
            await pipe.execute()


# -- Presence / heartbeat --------------------------------------------------

class RedisPresenceBackend:
    """Agent heartbeat tracking with Redis Hashes and a Sorted Set index."""

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _entry_key(self, tid: str, name: str) -> str:
        return f"t:{tid}:presence:{name}"

    def _index_key(self, tid: str) -> str:
        return f"t:{tid}:presence:_idx"

    async def heartbeat(self, tenant_id: str, name: str, status: str, room: str) -> dict:
        now = datetime.now(timezone.utc)
        now_iso, now_ts = now.isoformat(), now.timestamp()
        entry_key = self._entry_key(tenant_id, name)
        existing_start = await self._r.hget(entry_key, "uptime_start")
        entry = {
            "name": name, "status": status, "room": room,
            "last_heartbeat": now_iso,
            "uptime_start": existing_start or now_iso,
            "tenant_id": tenant_id,
        }
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hset(entry_key, mapping=entry)
            # No EXPIRE — sorted set tracks online/offline via score
            pipe.zadd(self._index_key(tenant_id), {name: now_ts})
            await pipe.execute()
        return entry

    async def list_all(self, tenant_id: str, timeout_seconds: int) -> list[dict]:
        # Return ALL entries; caller uses timeout_seconds to classify online/offline
        names = await self._r.zrange(self._index_key(tenant_id), 0, -1)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        ).timestamp()
        results: list[dict] = []
        for name in names:
            data = await self._r.hgetall(self._entry_key(tenant_id, name))
            if data:
                # Mark online/offline based on score
                score = await self._r.zscore(self._index_key(tenant_id), name)
                if score is not None and score >= cutoff:
                    data["_online"] = True
                else:
                    data["_online"] = False
                results.append(data)
        return results


# -- Rate limiting ----------------------------------------------------------

class RedisRateLimitBackend:
    """Sliding-window rate limiter using a Lua script for atomicity."""

    def __init__(self, r: Redis) -> None:
        self._r = r
        self._script = r.register_script(_RATE_LIMIT_LUA)

    def _key(self, tid: str, sender: str) -> str:
        return f"t:{tid}:rate:{sender}"

    async def check_and_increment(
        self, tenant_id: str, sender: str, window: int, max_count: int
    ) -> bool:
        result = await self._script(
            keys=[self._key(tenant_id, sender)],
            args=[time.time(), window, max_count],
        )
        return bool(result)


# -- SSE tokens -------------------------------------------------------------

class RedisSSETokenBackend:
    """Short-lived SSE auth tokens stored as Redis Strings."""

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _key(self, token: str) -> str:
        return f"ssetoken:{token}"

    async def create_token(self, tenant_id: str, recipient: str, ttl: int) -> str:
        token = uuid.uuid4().hex
        payload = json.dumps({"recipient": recipient, "tenant_id": tenant_id})
        await self._r.set(self._key(token), payload, ex=ttl)
        return token

    async def verify_token(self, token: str, recipient: str) -> tuple[bool, str]:
        raw = await self._r.getdel(self._key(token))
        if raw is None:
            return False, ""
        data = json.loads(raw)
        if data.get("recipient") != recipient:
            return False, ""
        return True, data.get("tenant_id", "")


# -- Webhooks (DM + room) --------------------------------------------------

class RedisWebhookBackend:
    """DM and room webhook registrations."""

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _dm_key(self, tid: str, name: str) -> str:
        return f"t:{tid}:webhook:dm:{name}"

    def _room_key(self, tid: str, rid: str) -> str:
        return f"t:{tid}:webhook:room:{rid}"

    async def register_dm(
        self, tenant_id: str, instance_name: str, callback_url: str
    ) -> None:
        await self._r.set(self._dm_key(tenant_id, instance_name), callback_url)

    async def get_dm(self, tenant_id: str, instance_name: str) -> str | None:
        return await self._r.get(self._dm_key(tenant_id, instance_name))

    async def delete_dm(self, tenant_id: str, instance_name: str) -> None:
        await self._r.delete(self._dm_key(tenant_id, instance_name))

    async def register_room(
        self, tenant_id: str, room_id: str, callback_url: str, registered_by: str,
    ) -> None:
        key = self._room_key(tenant_id, room_id)
        raw_hooks = await self._r.lrange(key, 0, -1)
        new_entry = json.dumps({"url": callback_url, "registered_by": registered_by})
        for raw in raw_hooks:
            hook = json.loads(raw)
            if hook["url"] == callback_url:
                await self._r.lrem(key, 1, raw)
                await self._r.rpush(key, new_entry)
                return
        await self._r.rpush(key, new_entry)

    async def list_room(self, tenant_id: str, room_id: str) -> list[dict]:
        raw = await self._r.lrange(self._room_key(tenant_id, room_id), 0, -1)
        return [json.loads(h) for h in raw]

    async def delete_room(
        self, tenant_id: str, room_id: str, callback_url: str
    ) -> bool:
        key = self._room_key(tenant_id, room_id)
        raw_hooks = await self._r.lrange(key, 0, -1)
        removed = False
        for raw in raw_hooks:
            if json.loads(raw)["url"] == callback_url:
                await self._r.lrem(key, 1, raw)
                removed = True
        return removed


# -- Analytics --------------------------------------------------------------

class RedisAnalyticsBackend:
    """Per-tenant send/delivery counters using Redis atomic increments."""

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _sent_key(self, tid: str) -> str:
        return f"t:{tid}:stats:sent"

    def _delivered_key(self, tid: str) -> str:
        return f"t:{tid}:stats:delivered"

    def _agent_key(self, tid: str) -> str:
        return f"t:{tid}:stats:agent"

    def _hourly_key(self, tid: str) -> str:
        return f"t:{tid}:stats:hourly"

    async def track_send(self, tenant_id: str, sender: str) -> None:
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        async with self._r.pipeline(transaction=False) as pipe:
            pipe.incr(self._sent_key(tenant_id))
            pipe.hincrby(self._agent_key(tenant_id), f"send:{sender}", 1)
            pipe.zincrby(self._hourly_key(tenant_id), 1, hour)
            await pipe.execute()

    async def track_delivery(self, tenant_id: str, recipient: str, count: int) -> None:
        async with self._r.pipeline(transaction=False) as pipe:
            pipe.incrby(self._delivered_key(tenant_id), count)
            pipe.hincrby(self._agent_key(tenant_id), f"recv:{recipient}", count)
            await pipe.execute()

    async def get_stats(self, tenant_id: str) -> dict:
        async with self._r.pipeline(transaction=False) as pipe:
            pipe.get(self._sent_key(tenant_id))
            pipe.get(self._delivered_key(tenant_id))
            pipe.hgetall(self._agent_key(tenant_id))
            results = await pipe.execute()
        total_sent = int(results[0] or 0)
        total_delivered = int(results[1] or 0)
        per_sender: dict[str, int] = {}
        per_recipient: dict[str, int] = {}
        for field, val in (results[2] or {}).items():
            if field.startswith("send:"):
                per_sender[field[5:]] = int(val)
            elif field.startswith("recv:"):
                per_recipient[field[5:]] = int(val)
        return {
            "total_sent": total_sent,
            "total_delivered": total_delivered,
            "per_sender": per_sender,
            "per_recipient": per_recipient,
        }


# -- Participants -----------------------------------------------------------

class RedisParticipantBackend:
    """Track known participant names in Redis Sets."""

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _key(self, tid: str) -> str:
        return f"t:{tid}:participants"

    async def add(self, tenant_id: str, *names: str) -> None:
        if names:
            await self._r.sadd(self._key(tenant_id), *names)

    async def list_all(self, tenant_id: str) -> list[str]:
        members = await self._r.smembers(self._key(tenant_id))
        return sorted(members)

    async def count_global(self) -> int:
        cursor, total = "0", 0
        while True:
            cursor, keys = await self._r.scan(
                cursor=cursor, match="t:*:participants", count=100
            )
            for key in keys:
                total += await self._r.scard(key)
            if cursor == 0 or cursor == "0":
                break
        return total


# -- Convenience bundle -----------------------------------------------------

@dataclass
class RedisBackends:
    """Holds all Redis backend instances for easy wiring."""

    messages: RedisMessageBackend
    rooms: RedisRoomBackend
    room_history: RedisRoomHistoryBackend
    presence: RedisPresenceBackend
    rate_limit: RedisRateLimitBackend
    sse_tokens: RedisSSETokenBackend
    webhooks: RedisWebhookBackend
    analytics: RedisAnalyticsBackend
    participants: RedisParticipantBackend = None  # type: ignore[assignment]

    @classmethod
    def create(cls, r: Redis, max_room_history: int = 200) -> RedisBackends:
        """Factory that instantiates every backend against *r*."""
        return cls(
            messages=RedisMessageBackend(r),
            rooms=RedisRoomBackend(r),
            room_history=RedisRoomHistoryBackend(r, max_history=max_room_history),
            presence=RedisPresenceBackend(r),
            rate_limit=RedisRateLimitBackend(r),
            sse_tokens=RedisSSETokenBackend(r),
            webhooks=RedisWebhookBackend(r),
            analytics=RedisAnalyticsBackend(r),
            participants=RedisParticipantBackend(r),
        )
