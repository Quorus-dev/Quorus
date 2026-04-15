"""Redis-backed implementations of all Quorus backend protocols.

Each class wraps a ``redis.asyncio.Redis`` instance and stores data
using the key schema documented in the production architecture spec.
All keys are tenant-scoped with the prefix ``t:{tenant_id}:``.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from redis.exceptions import ResponseError

_redis_logger = logging.getLogger("quorus.backends.redis")

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

# Lua: atomic room create (check name not taken, then create)
# KEYS[1]=name_index, KEYS[2]=meta_key, KEYS[3]=members_key, KEYS[4]=room_index
# ARGV[1]=room_name, ARGV[2]=room_id, ARGV[n...]=meta field/value pairs
# Returns 1 if created, 0 if name taken
_ROOM_CREATE_LUA = """
local name_idx = KEYS[1]
local meta_key = KEYS[2]
local members_key = KEYS[3]
local room_idx = KEYS[4]
local room_name = ARGV[1]
local room_id = ARGV[2]
if redis.call('HEXISTS', name_idx, room_name) == 1 then return 0 end
for i = 3, #ARGV, 2 do
  redis.call('HSET', meta_key, ARGV[i], ARGV[i+1])
end
redis.call('HSET', name_idx, room_name, room_id)
redis.call('SADD', room_idx, room_id)
return 1
"""

# Lua: atomic room rename (check new name not taken by different room)
# KEYS[1]=name_index, KEYS[2]=meta_key
# ARGV[1]=old_name, ARGV[2]=new_name, ARGV[3]=room_id
# Returns 1 if renamed, 0 if name taken
_ROOM_RENAME_LUA = """
local name_idx = KEYS[1]
local meta_key = KEYS[2]
local old_name = ARGV[1]
local new_name = ARGV[2]
local room_id = ARGV[3]
local existing = redis.call('HGET', name_idx, new_name)
if existing and existing ~= room_id then return 0 end
if old_name ~= '' then redis.call('HDEL', name_idx, old_name) end
redis.call('HSET', name_idx, new_name, room_id)
redis.call('HSET', meta_key, 'name', new_name)
return 1
"""

# Lua: atomic add member with capacity check + reverse index
# KEYS[1]=members_key, KEYS[2]=member_rooms_key
# ARGV[1]=name, ARGV[2]=role, ARGV[3]=max_members, ARGV[4]=room_id
# Returns 1 if added, 0 if at capacity
_ROOM_ADD_MEMBER_LUA = """
local members_key = KEYS[1]
local member_rooms = KEYS[2]
local name = ARGV[1]
local role = ARGV[2]
local max_members = tonumber(ARGV[3])
local room_id = ARGV[4]
if redis.call('HEXISTS', members_key, name) == 1 then
  redis.call('HSET', members_key, name, role)
  return 1
end
local count = redis.call('HLEN', members_key)
if count >= max_members then return 0 end
redis.call('HSET', members_key, name, role)
redis.call('SADD', member_rooms, room_id)
return 1
"""


# -- Messages (DM inboxes via Redis Streams) --------------------------------

_CONSUMER_GROUP = "quorus_cg"
_CONSUMER_NAME = "relay"

# Lua: atomic ACK+delete old entries then XADD new entries.
# KEYS[1] = stream key
# ARGV[1] = consumer group name
# ARGV[2] = number of old IDs (N)
# ARGV[3..2+N] = old entry IDs to ACK+DEL
# ARGV[3+N..] = JSON-encoded messages to XADD
_REQUEUE_LUA = """
local key = KEYS[1]
local group = ARGV[1]
local n_old = tonumber(ARGV[2])
-- ACK and delete old entries
for i = 3, 2 + n_old do
  redis.call('XACK', key, group, ARGV[i])
  redis.call('XDEL', key, ARGV[i])
end
-- Add new entries
for i = 3 + n_old, #ARGV do
  redis.call('XADD', key, '*', 'data', ARGV[i])
end
return 1
"""

# Lua: atomic quota-check + enqueue (no message loss)
# KEYS[n] = stream keys (one per recipient)
# ARGV[1] = maxlen (quota)
# ARGV[2..n+1] = JSON messages (one per recipient, same order as KEYS)
# Returns JSON array of indices (0-based) that were rejected due to quota
_QUOTA_ENQUEUE_LUA = """
local maxlen = tonumber(ARGV[1])
local rejected = {}
local num_keys = #KEYS

for i = 1, num_keys do
    local key = KEYS[i]
    local msg = ARGV[i + 1]
    local len = redis.call('XLEN', key)

    if len >= maxlen then
        -- Reject: queue is at capacity
        table.insert(rejected, i - 1)  -- 0-based index
    else
        -- Accept: add to stream
        redis.call('XADD', key, '*', 'data', msg)
    end
end

return cjson.encode(rejected)
"""


class RedisMessageBackend:
    """Per-recipient DM inbox backed by Redis Streams.

    Uses XADD/XREADGROUP/XACK for at-least-once delivery:
    - enqueue → XADD to the recipient's stream
    - fetch → XREADGROUP (new msgs) + XAUTOCLAIM (stale pending)
    - ack → XACK to confirm receipt
    - Unacked messages are automatically redelivered after VISIBILITY_TIMEOUT
    """

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _key(self, tid: str, name: str) -> str:
        return f"t:{tid}:dm:{name}"

    async def _ensure_group(self, key: str) -> None:
        """Create consumer group if it doesn't exist."""
        try:
            await self._r.xgroup_create(key, _CONSUMER_GROUP, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                _redis_logger.error("Unexpected error creating consumer group: %s", e)
                raise

    async def enqueue(self, tenant_id: str, to_name: str, message: dict) -> None:
        key = self._key(tenant_id, to_name)
        await self._ensure_group(key)
        # No MAXLEN trim — acked entries are deleted via XDEL in ack().
        # Stream growth is bounded by delivery rate, not blind trimming
        # that could drop unacked pending entries.
        await self._r.xadd(key, {"data": json.dumps(message)})

    async def enqueue_batch(
        self, tenant_id: str, to_name: str, messages: list[dict]
    ) -> None:
        if not messages:
            return
        key = self._key(tenant_id, to_name)
        await self._ensure_group(key)
        async with self._r.pipeline(transaction=False) as pipe:
            for m in messages:
                pipe.xadd(key, {"data": json.dumps(m)})
            await pipe.execute()

    async def enqueue_fanout(
        self, tenant_id: str, messages_by_recipient: dict[str, dict],
        maxlen: int | None = None,
    ) -> set[str]:
        """Fan-out: enqueue one message per recipient in a single pipeline.

        This batches all writes into a single Redis round-trip, reducing
        latency for room messages with many recipients from O(N) to O(1).

        Args:
            maxlen: If set, atomically check queue length and reject recipients
                    at capacity. This is true backpressure (no message loss).

        Returns:
            Set of recipient names that were rejected due to queue capacity.
        """
        if not messages_by_recipient:
            return set()

        # Ensure consumer groups exist for all recipients
        recipients = list(messages_by_recipient.keys())
        keys = [self._key(tenant_id, name) for name in recipients]
        for key in keys:
            await self._ensure_group(key)

        if maxlen:
            # Use Lua script for atomic quota-check + enqueue (no message loss)
            messages = [json.dumps(messages_by_recipient[r]) for r in recipients]
            result = await self._r.eval(
                _QUOTA_ENQUEUE_LUA,
                len(keys),
                *keys,
                maxlen,
                *messages,
            )
            rejected_indices = json.loads(result)
            return {recipients[i] for i in rejected_indices}
        else:
            # No quota: simple pipeline
            async with self._r.pipeline(transaction=False) as pipe:
                for recipient, message in messages_by_recipient.items():
                    key = self._key(tenant_id, recipient)
                    pipe.xadd(key, {"data": json.dumps(message)})
                await pipe.execute()
            return set()

    async def dequeue_all(self, tenant_id: str, to_name: str) -> list[dict]:
        """Read and acknowledge all messages (destructive read)."""
        key = self._key(tenant_id, to_name)
        await self._ensure_group(key)
        entries = await self._r.xreadgroup(
            _CONSUMER_GROUP, _CONSUMER_NAME, {key: ">"}, count=10000
        )
        if not entries:
            return []
        messages = []
        ids = []
        for stream_key, stream_entries in entries:
            for entry_id, fields in stream_entries:
                ids.append(entry_id)
                messages.append(json.loads(fields["data"]))
        if ids:
            await self._r.xack(key, _CONSUMER_GROUP, *ids)
            await self._r.xdel(key, *ids)
        return messages

    async def fetch(
        self, tenant_id: str, to_name: str
    ) -> tuple[list[dict], str]:
        key = self._key(tenant_id, to_name)
        await self._ensure_group(key)

        messages = []
        all_ids = []

        # 1. Reclaim stale pending messages (unacked past visibility timeout)
        visibility_ms = VISIBILITY_TIMEOUT * 1000
        try:
            claimed = await self._r.xautoclaim(
                key, _CONSUMER_GROUP, _CONSUMER_NAME,
                min_idle_time=visibility_ms, start_id="0-0", count=1000
            )
            # xautoclaim returns (next_start_id, claimed_entries, deleted_ids)
            if claimed and len(claimed) >= 2:
                for entry_id, fields in claimed[1]:
                    all_ids.append(entry_id)
                    msg = json.loads(fields["data"])
                    msg["_delivery_id"] = entry_id
                    messages.append(msg)
        except ResponseError as e:
            # XAUTOCLAIM requires Redis 6.2+. Log but don't fail.
            _redis_logger.warning(
                "XAUTOCLAIM failed (Redis 6.2+ required for auto-reclaim): %s", e
            )

        # 2. Read new messages
        entries = await self._r.xreadgroup(
            _CONSUMER_GROUP, _CONSUMER_NAME, {key: ">"}, count=10000
        )
        if entries:
            for stream_key, stream_entries in entries:
                for entry_id, fields in stream_entries:
                    all_ids.append(entry_id)
                    msg = json.loads(fields["data"])
                    msg["_delivery_id"] = entry_id
                    messages.append(msg)

        if not messages:
            return [], ""

        # ack_token = JSON list of stream entry IDs
        ack_token = json.dumps(all_ids)
        return messages, ack_token

    async def ack(
        self, tenant_id: str, to_name: str, ack_token: str
    ) -> None:
        if not ack_token:
            return
        key = self._key(tenant_id, to_name)
        try:
            ids = json.loads(ack_token)
        except (json.JSONDecodeError, TypeError):
            return
        if ids:
            await self._r.xack(key, _CONSUMER_GROUP, *ids)
            # Trim acked entries from the stream
            await self._r.xdel(key, *ids)

    async def ack_ids(
        self, tenant_id: str, to_name: str, message_ids: list[str]
    ) -> int:
        if not message_ids:
            return 0
        key = self._key(tenant_id, to_name)
        acked = await self._r.xack(key, _CONSUMER_GROUP, *message_ids)
        if message_ids:
            await self._r.xdel(key, *message_ids)
        return acked

    async def requeue(
        self,
        tenant_id: str,
        to_name: str,
        old_ids: list[str],
        messages: list[dict],
    ) -> None:
        """Atomically ACK+DEL old entries and XADD new entries via Lua."""
        if not old_ids and not messages:
            return
        key = self._key(tenant_id, to_name)
        args: list[str] = [_CONSUMER_GROUP, str(len(old_ids))]
        args.extend(old_ids)
        args.extend(json.dumps(m) for m in messages)
        await self._r.eval(_REQUEUE_LUA, 1, key, *args)

    async def pending_count(
        self, tenant_id: str, to_name: str
    ) -> int:
        key = self._key(tenant_id, to_name)
        try:
            info = await self._r.xpending(key, _CONSUMER_GROUP)
            return info["pending"] if info else 0
        except ResponseError:
            return 0  # No consumer group yet

    async def peek(self, tenant_id: str, to_name: str) -> int:
        return await self._r.xlen(self._key(tenant_id, to_name))

    async def count_all(self, tenant_id: str) -> int:
        """Count total messages across all recipient streams for a tenant."""
        cursor, total = "0", 0
        while True:
            cursor, keys = await self._r.scan(
                cursor=cursor, match=f"t:{tenant_id}:dm:*", count=100
            )
            for key in keys:
                total += await self._r.xlen(key)
            if cursor == 0 or cursor == "0":
                break
        return total

    async def count_all_global(self) -> int:
        """Count total messages globally across all streams."""
        cursor, total = "0", 0
        while True:
            cursor, keys = await self._r.scan(
                cursor=cursor, match="t:*:dm:*", count=100
            )
            for key in keys:
                total += await self._r.xlen(key)
            if cursor == 0 or cursor == "0":
                break
        return total

    async def recipient_depth(self, tenant_id: str, to_name: str) -> int:
        """Return total stream length for a recipient."""
        return await self._r.xlen(self._key(tenant_id, to_name))


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

    def _member_rooms_key(self, tid: str, name: str) -> str:
        return f"t:{tid}:member:{name}:rooms"

    async def create(self, tenant_id: str, room_id: str, room_data: dict) -> None:
        meta = {k: v for k, v in room_data.items() if k != "members"}
        members = room_data.get("members", {})
        async with self._r.pipeline(transaction=True) as pipe:
            if meta:
                pipe.hset(self._meta_key(tenant_id, room_id), mapping=meta)
            if members:
                pipe.hset(self._members_key(tenant_id, room_id), mapping=members)
                # Maintain reverse index: member -> rooms
                for member_name in members:
                    pipe.sadd(self._member_rooms_key(tenant_id, member_name), room_id)
            name = room_data.get("name")
            if name:
                pipe.hset(self._name_index_key(tenant_id), name, room_id)
            pipe.sadd(self._room_index_key(tenant_id), room_id)
            await pipe.execute()

    async def create_if_name_available(
        self, tenant_id: str, room_id: str, room_data: dict
    ) -> bool:
        meta = {k: v for k, v in room_data.items() if k != "members"}
        name = room_data.get("name", "")
        if not name:
            await self.create(tenant_id, room_id, room_data)
            return True
        # Flatten meta dict to alternating key/value args for Lua
        meta_args: list[str] = []
        for k, v in meta.items():
            meta_args.extend([k, str(v)])
        result = await self._r.eval(
            _ROOM_CREATE_LUA,
            4,
            self._name_index_key(tenant_id),
            self._meta_key(tenant_id, room_id),
            self._members_key(tenant_id, room_id),
            self._room_index_key(tenant_id),
            name,
            room_id,
            *meta_args,
        )
        if result == 0:
            return False
        # Add members separately (pipeline is fine, already created atomically)
        members = room_data.get("members", {})
        if members:
            await self._r.hset(
                self._members_key(tenant_id, room_id), mapping=members
            )
        return True

    async def rename_if_available(
        self, tenant_id: str, room_id: str, new_name: str
    ) -> bool:
        meta_key = self._meta_key(tenant_id, room_id)
        old_name = await self._r.hget(meta_key, "name") or ""
        result = await self._r.eval(
            _ROOM_RENAME_LUA,
            2,
            self._name_index_key(tenant_id),
            meta_key,
            old_name,
            new_name,
            room_id,
        )
        return result == 1

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

    async def list_by_member(
        self, tenant_id: str, member_name: str
    ) -> list[tuple[str, dict]]:
        """Return rooms where member_name is a member via reverse index."""
        room_ids = await self._r.smembers(
            self._member_rooms_key(tenant_id, member_name)
        )
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
        members_key = self._members_key(tenant_id, room_id)
        name = await self._r.hget(meta_key, "name")
        # Get all members to clean up reverse index
        members = await self._r.hkeys(members_key)
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.delete(meta_key)
            pipe.delete(members_key)
            if name:
                pipe.hdel(self._name_index_key(tenant_id), name)
            pipe.srem(self._room_index_key(tenant_id), room_id)
            # Clean up reverse index for all members
            for member_name in members:
                pipe.srem(self._member_rooms_key(tenant_id, member_name), room_id)
            await pipe.execute()

    async def add_member(
        self, tenant_id: str, room_id: str, name: str, role: str
    ) -> None:
        if not await self._r.exists(self._meta_key(tenant_id, room_id)):
            return
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hset(self._members_key(tenant_id, room_id), name, role)
            pipe.sadd(self._member_rooms_key(tenant_id, name), room_id)
            await pipe.execute()

    async def add_member_if_capacity(
        self, tenant_id: str, room_id: str, name: str, role: str, max_members: int
    ) -> bool:
        result = await self._r.eval(
            _ROOM_ADD_MEMBER_LUA,
            2,
            self._members_key(tenant_id, room_id),
            self._member_rooms_key(tenant_id, name),
            name,
            role,
            str(max_members),
            room_id,
        )
        return result == 1

    async def remove_member(self, tenant_id: str, room_id: str, name: str) -> None:
        if not await self._r.exists(self._meta_key(tenant_id, room_id)):
            return
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hdel(self._members_key(tenant_id, room_id), name)
            pipe.srem(self._member_rooms_key(tenant_id, name), room_id)
            await pipe.execute()

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
    """DM and room webhook registrations with per-webhook secrets."""

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _dm_key(self, tid: str, name: str) -> str:
        return f"t:{tid}:webhook:dm:{name}"

    def _room_key(self, tid: str, rid: str) -> str:
        return f"t:{tid}:webhook:room:{rid}"

    async def register_dm(
        self,
        tenant_id: str,
        instance_name: str,
        callback_url: str,
        secret: str = "",
    ) -> None:
        data = json.dumps({"url": callback_url, "secret": secret})
        await self._r.set(self._dm_key(tenant_id, instance_name), data)

    async def get_dm(self, tenant_id: str, instance_name: str) -> dict | None:
        raw = await self._r.get(self._dm_key(tenant_id, instance_name))
        if not raw:
            return None
        # Handle legacy format (plain URL string)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"url": raw, "secret": ""}

    async def delete_dm(self, tenant_id: str, instance_name: str) -> None:
        await self._r.delete(self._dm_key(tenant_id, instance_name))

    async def register_room(
        self,
        tenant_id: str,
        room_id: str,
        callback_url: str,
        registered_by: str,
        secret: str = "",
    ) -> None:
        key = self._room_key(tenant_id, room_id)
        raw_hooks = await self._r.lrange(key, 0, -1)
        new_entry = json.dumps({
            "url": callback_url,
            "registered_by": registered_by,
            "secret": secret,
        })
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


# -- Idempotency ------------------------------------------------------------

_IDEMPOTENCY_PENDING = "__pending__"


class RedisIdempotencyBackend:
    """Idempotency key storage using Redis strings with TTL.

    Supports atomic reservation to prevent race conditions:
    - reserve() uses SET NX to atomically claim a key
    - set() stores the final result
    - get() retrieves cached results

    Body fingerprinting ensures that reusing an idempotency key with a
    different request body returns 409 Conflict (per RFC).
    """

    def __init__(self, r: Redis) -> None:
        self._r = r

    def _key(self, tid: str, key: str) -> str:
        return f"t:{tid}:idempotency:{key}"

    async def get(self, tenant_id: str, key: str) -> dict | None:
        cached = await self._r.get(self._key(tenant_id, key))
        if cached:
            data = json.loads(cached)
            # Don't return pending markers as cached results
            if data.get("_status") == _IDEMPOTENCY_PENDING:
                return None
            # Strip internal fields before returning
            result = data.get("_result")
            return result
        return None

    async def reserve(
        self, tenant_id: str, key: str, body_fingerprint: str, ttl: int
    ) -> dict | None:
        """Atomically reserve a key for processing.

        Args:
            tenant_id: Tenant identifier
            key: The idempotency key (without body fingerprint)
            body_fingerprint: SHA256 hash of the request body
            ttl: Time-to-live in seconds

        Returns:
        - None if reservation succeeded (caller should process request)
        - dict with cached result if key has a completed result with matching body
        Raises HTTPException(409) if:
        - Key is reserved but not yet complete (concurrent request)
        - Key exists with a different body fingerprint (key reuse violation)
        """
        from fastapi import HTTPException

        redis_key = self._key(tenant_id, key)
        pending_value = json.dumps({
            "_status": _IDEMPOTENCY_PENDING,
            "_body_fp": body_fingerprint,
        })

        # Try to set the key only if it doesn't exist
        reserved = await self._r.set(redis_key, pending_value, ex=ttl, nx=True)

        if reserved:
            # We got the lock, caller should process the request
            return None

        # Key already exists — check status and body fingerprint
        cached = await self._r.get(redis_key)
        if cached:
            data = json.loads(cached)

            # Check body fingerprint first
            stored_fp = data.get("_body_fp")
            if stored_fp and stored_fp != body_fingerprint:
                # Same key, different body = violation
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key already used with different request body",
                )

            if data.get("_status") == _IDEMPOTENCY_PENDING:
                # Another request is still processing this key
                raise HTTPException(
                    status_code=409,
                    detail="Request with this Idempotency-Key is already in progress",
                )

            # Return the completed result
            return data.get("_result")

        # Key expired between set and get — try again
        return await self.reserve(tenant_id, key, body_fingerprint, ttl)

    async def set(
        self, tenant_id: str, key: str, body_fingerprint: str, result: dict, ttl: int
    ) -> None:
        """Store the completed result with body fingerprint."""
        value = json.dumps({
            "_body_fp": body_fingerprint,
            "_result": result,
        })
        await self._r.set(self._key(tenant_id, key), value, ex=ttl)

    async def delete(self, tenant_id: str, key: str) -> None:
        await self._r.delete(self._key(tenant_id, key))


# -- Webhook delivery queue -------------------------------------------------

_WEBHOOK_QUEUE_KEY = "webhook:queue"
_WEBHOOK_DELAY_KEY = "webhook:delay"  # Sorted set for delayed retries
_WEBHOOK_DLQ_KEY = "webhook:dlq"
_WEBHOOK_CG = "webhook_workers"
_WEBHOOK_CONSUMER = "worker"
_WEBHOOK_VISIBILITY_TIMEOUT = int(os.environ.get("WEBHOOK_VISIBILITY_TIMEOUT", "30"))
_WEBHOOK_BACKOFF_BASE = int(os.environ.get("WEBHOOK_BACKOFF_BASE", "2"))  # seconds
_DLQ_MAX_SIZE = 1000

# Lua: atomic NACK — ACK+DEL old entry, then either ZADD to delay set or LPUSH to DLQ
# KEYS[1] = queue stream, KEYS[2] = delay sorted set, KEYS[3] = DLQ list
# ARGV[1] = consumer group, ARGV[2] = job_id, ARGV[3] = updated job JSON
# ARGV[4] = max_retries, ARGV[5] = current attempt, ARGV[6] = dlq_max_size
# ARGV[7] = retry_at timestamp (when to retry)
# Returns 1 if scheduled for retry, 0 if moved to DLQ
_WEBHOOK_NACK_LUA = """
local queue = KEYS[1]
local delay_set = KEYS[2]
local dlq = KEYS[3]
local cg = ARGV[1]
local job_id = ARGV[2]
local job_json = ARGV[3]
local max_retries = tonumber(ARGV[4])
local attempt = tonumber(ARGV[5])
local dlq_max = tonumber(ARGV[6])
local retry_at = tonumber(ARGV[7])

-- ACK and delete old entry atomically
redis.call('XACK', queue, cg, job_id)
redis.call('XDEL', queue, job_id)

if attempt >= max_retries then
    -- Move to DLQ
    redis.call('LPUSH', dlq, job_json)
    redis.call('LTRIM', dlq, 0, dlq_max - 1)
    return 0
else
    -- Add to delay set with retry_at as score
    redis.call('ZADD', delay_set, retry_at, job_json)
    return 1
end
"""

# Lua: promote due jobs from delay set to stream
# KEYS[1] = delay sorted set, KEYS[2] = queue stream
# ARGV[1] = current timestamp, ARGV[2] = max jobs to promote
# Returns number of jobs promoted
_WEBHOOK_PROMOTE_LUA = """
local delay_set = KEYS[1]
local queue = KEYS[2]
local now = tonumber(ARGV[1])
local max_promote = tonumber(ARGV[2])

local due = redis.call('ZRANGEBYSCORE', delay_set, 0, now, 'LIMIT', 0, max_promote)
local promoted = 0
for _, job_json in ipairs(due) do
    redis.call('XADD', queue, '*', 'data', job_json)
    redis.call('ZREM', delay_set, job_json)
    promoted = promoted + 1
end
return promoted
"""


class RedisWebhookQueueBackend:
    """Durable webhook delivery queue using Redis Streams.

    Provides at-least-once delivery for webhook jobs:
    - enqueue → XADD to the queue stream
    - fetch → XREADGROUP (new) + XAUTOCLAIM (stale pending)
    - ack → XACK + XDEL on success
    - nack → increment attempt, re-enqueue or move to DLQ
    """

    def __init__(self, r: Redis) -> None:
        self._r = r
        self._group_created = False

    async def _ensure_group(self) -> None:
        """Create consumer group if it doesn't exist."""
        if self._group_created:
            return
        try:
            await self._r.xgroup_create(
                _WEBHOOK_QUEUE_KEY, _WEBHOOK_CG, id="0", mkstream=True
            )
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        self._group_created = True

    async def enqueue(self, job: dict) -> str:
        """Add a webhook job to the queue. Returns job ID."""
        await self._ensure_group()
        job_id = await self._r.xadd(_WEBHOOK_QUEUE_KEY, {"data": json.dumps(job)})
        return job_id

    async def fetch(self, count: int = 10) -> list[tuple[str, dict]]:
        """Fetch pending jobs for delivery."""
        await self._ensure_group()
        jobs: list[tuple[str, dict]] = []

        # 0. Promote any due delayed jobs back to the main queue
        await self.promote_delayed()

        # 1. Reclaim stale pending jobs (visibility timeout expired)
        visibility_ms = _WEBHOOK_VISIBILITY_TIMEOUT * 1000
        try:
            claimed = await self._r.xautoclaim(
                _WEBHOOK_QUEUE_KEY, _WEBHOOK_CG, _WEBHOOK_CONSUMER,
                min_idle_time=visibility_ms, start_id="0-0", count=count
            )
            if claimed and len(claimed) >= 2:
                for entry_id, fields in claimed[1]:
                    job = json.loads(fields["data"])
                    jobs.append((entry_id, job))
        except ResponseError as e:
            _redis_logger.warning("XAUTOCLAIM failed: %s", e)

        # 2. Read new jobs
        remaining = count - len(jobs)
        if remaining > 0:
            entries = await self._r.xreadgroup(
                _WEBHOOK_CG, _WEBHOOK_CONSUMER,
                {_WEBHOOK_QUEUE_KEY: ">"}, count=remaining
            )
            if entries:
                for _stream_key, stream_entries in entries:
                    for entry_id, fields in stream_entries:
                        job = json.loads(fields["data"])
                        jobs.append((entry_id, job))

        return jobs

    async def ack(self, job_id: str) -> None:
        """Acknowledge successful delivery, removing the job."""
        await self._r.xack(_WEBHOOK_QUEUE_KEY, _WEBHOOK_CG, job_id)
        await self._r.xdel(_WEBHOOK_QUEUE_KEY, job_id)

    async def nack(
        self, job_id: str, error: str, max_retries: int
    ) -> bool:
        """Mark job as failed. Returns True if job will be retried.

        Uses exponential backoff: retry delays are 2s, 4s, 8s, etc.
        Failed jobs go to a delay sorted set and are promoted back to
        the main stream when their retry time arrives.

        Uses a Lua script to atomically ACK+DEL the old entry and either
        add to delay set for retry or move to DLQ. This eliminates the
        crash window where a job could be lost.
        """
        # Fetch the job data first (read doesn't need to be atomic)
        entries = await self._r.xrange(_WEBHOOK_QUEUE_KEY, job_id, job_id)
        if not entries:
            return False

        job = json.loads(entries[0][1]["data"])
        job["attempt"] = job.get("attempt", 0) + 1
        job["last_error"] = error

        # Calculate retry time with exponential backoff: 2^attempt seconds
        delay_seconds = _WEBHOOK_BACKOFF_BASE ** job["attempt"]
        retry_at = time.time() + delay_seconds

        # Atomic NACK via Lua script
        result = await self._r.eval(
            _WEBHOOK_NACK_LUA,
            3,  # number of keys
            _WEBHOOK_QUEUE_KEY,
            _WEBHOOK_DELAY_KEY,
            _WEBHOOK_DLQ_KEY,
            _WEBHOOK_CG,
            job_id,
            json.dumps(job),
            str(max_retries),
            str(job["attempt"]),
            str(_DLQ_MAX_SIZE),
            str(retry_at),
        )
        return result == 1

    async def promote_delayed(self, max_promote: int = 100) -> int:
        """Move due jobs from delay set to main queue.

        Should be called periodically by workers before fetching.
        Returns the number of jobs promoted.
        """
        now = time.time()
        result = await self._r.eval(
            _WEBHOOK_PROMOTE_LUA,
            2,  # number of keys
            _WEBHOOK_DELAY_KEY,
            _WEBHOOK_QUEUE_KEY,
            str(now),
            str(max_promote),
        )
        return int(result) if result else 0

    async def get_dlq(self, limit: int = 50) -> list[dict]:
        """Return recent dead letter queue entries."""
        raw = await self._r.lrange(_WEBHOOK_DLQ_KEY, 0, limit - 1)
        return [json.loads(j) for j in raw]

    async def get_stats(self) -> dict:
        """Return queue statistics."""
        try:
            queue_len = await self._r.xlen(_WEBHOOK_QUEUE_KEY)
        except ResponseError:
            queue_len = 0

        try:
            pending_info = await self._r.xpending(_WEBHOOK_QUEUE_KEY, _WEBHOOK_CG)
            pending = pending_info["pending"] if pending_info else 0
        except ResponseError:
            pending = 0

        delayed = await self._r.zcard(_WEBHOOK_DELAY_KEY)
        dlq_size = await self._r.llen(_WEBHOOK_DLQ_KEY)

        return {
            "queue_length": queue_len,
            "pending": pending,
            "delayed": delayed,
            "dlq_size": dlq_size,
        }


# -- Room State (Shared State Matrix) --------------------------------------


class RedisRoomStateBackend:
    """Redis-backed room coordination state for distributed mutex.

    Key schema:
    - t:{tid}:room_state:{rid}:goal — string (active goal)
    - t:{tid}:room_state:{rid}:tasks — list of JSON tasks
    - t:{tid}:room_state:{rid}:locks — hash (file_path -> JSON lock_data)
    - t:{tid}:room_state:{rid}:decisions — list of JSON decisions

    Atomic operations use Lua scripts to prevent TOCTOU race conditions
    in distributed deployments.
    """

    # Lua script: atomic lock acquire
    # Returns: [0, existing_lock_data] if already locked, [1, nil] if acquired
    _LUA_ACQUIRE_LOCK = """
    local locks_key = KEYS[1]
    local tasks_key = KEYS[2]
    local file_path = ARGV[1]
    local lock_data = ARGV[2]
    local task_data = ARGV[3]

    -- Check if lock already exists (HSETNX returns 0 if key exists)
    local acquired = redis.call('HSETNX', locks_key, file_path, lock_data)
    if acquired == 0 then
        -- Lock already held, return existing data
        local existing = redis.call('HGET', locks_key, file_path)
        return {0, existing}
    end

    -- Lock acquired, append task
    redis.call('RPUSH', tasks_key, task_data)
    return {1, nil}
    """

    # Lua script: atomic lock release with token verification
    # Returns: 0 if no lock, 1 if token mismatch, 2 if released
    _LUA_RELEASE_LOCK = """
    local locks_key = KEYS[1]
    local tasks_key = KEYS[2]
    local file_path = ARGV[1]
    local expected_token = ARGV[2]

    -- Get current lock
    local lock_json = redis.call('HGET', locks_key, file_path)
    if not lock_json then
        return {0, nil}  -- No lock exists
    end

    -- Parse and verify token
    local lock = cjson.decode(lock_json)
    if lock.lock_token ~= expected_token then
        return {1, lock_json}  -- Token mismatch
    end

    -- Token matches, release the lock
    redis.call('HDEL', locks_key, file_path)

    -- Remove from tasks list (find and remove matching task)
    local tasks = redis.call('LRANGE', tasks_key, 0, -1)
    if #tasks > 0 then
        redis.call('DEL', tasks_key)
        for _, task_json in ipairs(tasks) do
            local task = cjson.decode(task_json)
            if task.file_path ~= file_path or task.lock_token ~= expected_token then
                redis.call('RPUSH', tasks_key, task_json)
            end
        end
    end

    return {2, nil}  -- Successfully released
    """

    # Lua script: atomic task expiry with token-verified lock cleanup
    # Returns: JSON array of expired task IDs
    # Only removes a lock if the stored token matches the expired task's token
    # Uses numeric expires_at_epoch for accurate comparison (no ISO parsing)
    # MIGRATION: Tasks without expires_at_epoch are treated as expired (legacy cleanup)
    _LUA_EXPIRE_TASKS = """
    local tasks_key = KEYS[1]
    local locks_key = KEYS[2]
    local now_ts = tonumber(ARGV[1])

    local tasks = redis.call('LRANGE', tasks_key, 0, -1)
    if #tasks == 0 then
        return '[]'
    end

    local expired_ids = {}
    local live_tasks = {}

    for _, task_json in ipairs(tasks) do
        local task = cjson.decode(task_json)
        -- Use numeric epoch timestamp (stored as expires_at_epoch)
        local exp_ts = task.expires_at_epoch

        -- MIGRATION: Tasks without expires_at_epoch are from the old format
        -- and should be expired to prevent permanent zombie tasks
        local is_expired = false
        if exp_ts == nil then
            -- Legacy task without epoch timestamp — expire it
            is_expired = true
        elseif exp_ts < now_ts then
            -- Normal expiry check
            is_expired = true
        end

        if is_expired then
            -- Task expired
            table.insert(expired_ids, task.id)

            -- Only delete lock if token still matches (prevents race with new acquisition)
            local file_path = task.file_path
            local task_token = task.lock_token
            if file_path and task_token then
                local lock_json = redis.call('HGET', locks_key, file_path)
                if lock_json then
                    local lock = cjson.decode(lock_json)
                    if lock.lock_token == task_token then
                        redis.call('HDEL', locks_key, file_path)
                    end
                end
            end
        else
            -- Task still valid
            table.insert(live_tasks, task_json)
        end
    end

    -- Rebuild tasks list atomically
    if #expired_ids > 0 then
        redis.call('DEL', tasks_key)
        if #live_tasks > 0 then
            redis.call('RPUSH', tasks_key, unpack(live_tasks))
        end
    end

    return cjson.encode(expired_ids)
    """

    def __init__(self, r: Redis) -> None:
        self._r = r
        self._acquire_script = None
        self._release_script = None
        self._expire_script = None

    async def _get_acquire_script(self):
        """Lazily register the acquire lock Lua script."""
        if self._acquire_script is None:
            self._acquire_script = self._r.register_script(self._LUA_ACQUIRE_LOCK)
        return self._acquire_script

    async def _get_release_script(self):
        """Lazily register the release lock Lua script."""
        if self._release_script is None:
            self._release_script = self._r.register_script(self._LUA_RELEASE_LOCK)
        return self._release_script

    async def _get_expire_script(self):
        """Lazily register the task expiry Lua script."""
        if self._expire_script is None:
            self._expire_script = self._r.register_script(self._LUA_EXPIRE_TASKS)
        return self._expire_script

    def _key(self, tid: str, rid: str, suffix: str) -> str:
        return f"t:{tid}:room_state:{rid}:{suffix}"

    async def get(self, tenant_id: str, room_id: str) -> dict:
        """Return a snapshot of the room's coordination state."""
        goal_key = self._key(tenant_id, room_id, "goal")
        tasks_key = self._key(tenant_id, room_id, "tasks")
        locks_key = self._key(tenant_id, room_id, "locks")
        decisions_key = self._key(tenant_id, room_id, "decisions")

        pipe = self._r.pipeline()
        pipe.get(goal_key)
        pipe.lrange(tasks_key, 0, -1)
        pipe.hgetall(locks_key)
        pipe.lrange(decisions_key, 0, -1)
        goal, tasks_raw, locks_raw, decisions_raw = await pipe.execute()

        claimed_tasks = [json.loads(t) for t in tasks_raw] if tasks_raw else []
        locked_files = {
            k: json.loads(v) for k, v in locks_raw.items()
        } if locks_raw else {}
        resolved_decisions = [
            json.loads(d) for d in decisions_raw
        ] if decisions_raw else []

        return {
            "active_goal": goal,
            "claimed_tasks": claimed_tasks,
            "locked_files": locked_files,
            "resolved_decisions": resolved_decisions,
        }

    async def set_goal(
        self, tenant_id: str, room_id: str, goal: str | None
    ) -> None:
        """Set (or clear) the active goal for a room."""
        key = self._key(tenant_id, room_id, "goal")
        if goal is None:
            await self._r.delete(key)
        else:
            await self._r.set(key, goal)

    async def add_claimed_task(
        self, tenant_id: str, room_id: str, task: dict
    ) -> None:
        """Record a newly acquired file lock as a claimed task."""
        tasks_key = self._key(tenant_id, room_id, "tasks")
        locks_key = self._key(tenant_id, room_id, "locks")

        pipe = self._r.pipeline()
        pipe.rpush(tasks_key, json.dumps(task))

        # Also write the file lock entry
        fp = task.get("file_path")
        if fp:
            lock_data = {
                "held_by": task["claimed_by"],
                "lock_token": task["lock_token"],
                "expires_at": task["expires_at"],
            }
            pipe.hset(locks_key, fp, json.dumps(lock_data))

        await pipe.execute()

    async def remove_claimed_task(
        self, tenant_id: str, room_id: str, task_id: str
    ) -> None:
        """Remove a claimed task (on release or manual clear)."""
        tasks_key = self._key(tenant_id, room_id, "tasks")
        locks_key = self._key(tenant_id, room_id, "locks")

        # Get all tasks, filter out the one to remove, write back
        tasks_raw = await self._r.lrange(tasks_key, 0, -1)
        if not tasks_raw:
            return

        new_tasks = []
        removed_fp = None
        for t in tasks_raw:
            task = json.loads(t)
            if task.get("id") == task_id:
                removed_fp = task.get("file_path")
            else:
                new_tasks.append(t)

        pipe = self._r.pipeline()
        pipe.delete(tasks_key)
        if new_tasks:
            pipe.rpush(tasks_key, *new_tasks)
        if removed_fp:
            pipe.hdel(locks_key, removed_fp)
        await pipe.execute()

    async def set_lock(
        self, tenant_id: str, room_id: str, file_path: str, lock_data: dict
    ) -> None:
        """Directly set a lock entry."""
        key = self._key(tenant_id, room_id, "locks")
        await self._r.hset(key, file_path, json.dumps(lock_data))

    async def release_lock(
        self, tenant_id: str, room_id: str, file_path: str
    ) -> None:
        """Remove a file lock entry."""
        key = self._key(tenant_id, room_id, "locks")
        await self._r.hdel(key, file_path)

    async def try_acquire_lock(
        self, tenant_id: str, room_id: str, file_path: str,
        lock_data: dict, task_data: dict
    ) -> tuple[bool, dict | None]:
        """Atomically try to acquire a lock.

        Uses a Lua script to prevent TOCTOU race conditions where two replicas
        both read "unlocked" and both grant the lock.

        Returns:
            (True, None) if lock was acquired
            (False, existing_lock_data) if lock is already held
        """
        locks_key = self._key(tenant_id, room_id, "locks")
        tasks_key = self._key(tenant_id, room_id, "tasks")

        script = await self._get_acquire_script()
        result = await script(
            keys=[locks_key, tasks_key],
            args=[file_path, json.dumps(lock_data), json.dumps(task_data)],
        )

        acquired = result[0] == 1
        if acquired:
            return (True, None)
        else:
            existing = json.loads(result[1]) if result[1] else None
            return (False, existing)

    async def release_lock_atomic(
        self, tenant_id: str, room_id: str, file_path: str, lock_token: str
    ) -> tuple[str, dict | None]:
        """Atomically release a lock with token verification.

        Uses a Lua script to prevent race conditions where another agent
        acquires a new lock between the token check and the release.

        Returns:
            ("not_found", None) if no lock exists
            ("token_mismatch", existing_lock) if token doesn't match
            ("released", None) if successfully released
        """
        locks_key = self._key(tenant_id, room_id, "locks")
        tasks_key = self._key(tenant_id, room_id, "tasks")

        script = await self._get_release_script()
        result = await script(
            keys=[locks_key, tasks_key],
            args=[file_path, lock_token],
        )

        status_code = result[0]
        if status_code == 0:
            return ("not_found", None)
        elif status_code == 1:
            existing = json.loads(result[1]) if result[1] else None
            return ("token_mismatch", existing)
        else:
            return ("released", None)

    async def add_decision(
        self, tenant_id: str, room_id: str, decision: dict
    ) -> None:
        """Append a resolved decision to the room's decision log."""
        key = self._key(tenant_id, room_id, "decisions")
        await self._r.rpush(key, json.dumps(decision))

    async def expire_tasks(
        self, tenant_id: str, room_id: str
    ) -> list[str]:
        """Remove and return IDs of tasks whose TTL has elapsed.

        Uses a Lua script for atomicity — only removes a lock if the stored
        token matches the expired task's token. This prevents race conditions
        where a new lock is acquired between reading and deleting.
        """
        tasks_key = self._key(tenant_id, room_id, "tasks")
        locks_key = self._key(tenant_id, room_id, "locks")

        script = await self._get_expire_script()
        now_ts = int(time.time())

        result = await script(
            keys=[tasks_key, locks_key],
            args=[now_ts],
        )

        # Result is JSON array of expired task IDs
        if result:
            return json.loads(result)
        return []


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
    idempotency: RedisIdempotencyBackend = None  # type: ignore[assignment]
    webhook_queue: RedisWebhookQueueBackend = None  # type: ignore[assignment]
    room_state: RedisRoomStateBackend = None  # type: ignore[assignment]

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
            idempotency=RedisIdempotencyBackend(r),
            webhook_queue=RedisWebhookQueueBackend(r),
            room_state=RedisRoomStateBackend(r),
        )
