"""In-memory implementations of all Murmur backend protocols.

All state is tenant-scoped using ``(tenant_id, key)`` tuple keys or nested
dicts.  Every mutable operation is protected by an ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

# -- Messages ---------------------------------------------------------------


class InMemoryMessageBackend:
    """Per-recipient DM inbox with at-least-once delivery semantics.

    Messages move through three states:
    1. Queued — waiting in the inbox
    2. Pending — fetched but not yet acknowledged (redelivered after timeout)
    3. Acknowledged — removed permanently
    """

    VISIBILITY_TIMEOUT = 60  # seconds before unacked messages are redelivered

    def __init__(self) -> None:
        self._queues: dict[tuple[str, str], list[dict]] = defaultdict(list)
        # Pending messages: (tenant_id, to_name, ack_token) -> (messages, fetch_time)
        self._pending: dict[tuple[str, str, str], tuple[list[dict], float]] = {}
        # Per-message ID -> (tenant_id, to_name, ack_token) for ack_ids
        self._msg_id_index: dict[str, tuple[str, str, str]] = {}
        self._lock = asyncio.Lock()

    def _redeliver_stale(self, tenant_id: str, to_name: str) -> None:
        """Move timed-out pending messages back to the queue (call under lock)."""
        now = time.time()
        stale_keys = []
        for (tid, name, token), (msgs, fetch_time) in self._pending.items():
            if tid == tenant_id and name == to_name:
                if now - fetch_time > self.VISIBILITY_TIMEOUT:
                    stale_keys.append((tid, name, token))
        for key in stale_keys:
            msgs, _ = self._pending.pop(key)
            self._queues[(tenant_id, to_name)].extend(msgs)
            for m in msgs:
                mid = m.get("id", "")
                self._msg_id_index.pop(mid, None)

    async def enqueue(
        self, tenant_id: str, to_name: str, message: dict
    ) -> None:
        async with self._lock:
            self._queues[(tenant_id, to_name)].append(message)

    async def enqueue_batch(
        self, tenant_id: str, to_name: str, messages: list[dict]
    ) -> None:
        async with self._lock:
            self._queues[(tenant_id, to_name)].extend(messages)

    async def dequeue_all(self, tenant_id: str, to_name: str) -> list[dict]:
        async with self._lock:
            key = (tenant_id, to_name)
            msgs = list(self._queues[key])
            self._queues[key].clear()
            return msgs

    async def fetch(
        self, tenant_id: str, to_name: str
    ) -> tuple[list[dict], str]:
        async with self._lock:
            # Redeliver stale pending messages first
            self._redeliver_stale(tenant_id, to_name)
            key = (tenant_id, to_name)
            msgs = list(self._queues[key])
            if not msgs:
                return [], ""
            self._queues[key].clear()
            token = uuid.uuid4().hex
            # Inject _delivery_id for client-side per-message ACK
            for m in msgs:
                mid = m.get("id", "")
                if mid:
                    m["_delivery_id"] = mid
            self._pending[(tenant_id, to_name, token)] = (msgs, time.time())
            for m in msgs:
                mid = m.get("id", "")
                if mid:
                    self._msg_id_index[mid] = (tenant_id, to_name, token)
            return msgs, token

    async def ack(
        self, tenant_id: str, to_name: str, ack_token: str
    ) -> None:
        async with self._lock:
            entry = self._pending.pop((tenant_id, to_name, ack_token), None)
            if entry:
                msgs, _ = entry
                for m in msgs:
                    self._msg_id_index.pop(m.get("id", ""), None)

    async def ack_ids(
        self, tenant_id: str, to_name: str, message_ids: list[str]
    ) -> int:
        async with self._lock:
            acked = 0
            for mid in message_ids:
                key = self._msg_id_index.pop(mid, None)
                if key is None:
                    continue
                tid, name, token = key
                if tid != tenant_id or name != to_name:
                    continue
                entry = self._pending.get(key)
                if entry:
                    msgs, fetch_time = entry
                    msgs[:] = [m for m in msgs if m.get("id") != mid]
                    if not msgs:
                        self._pending.pop(key, None)
                    acked += 1
            return acked

    async def requeue(
        self,
        tenant_id: str,
        to_name: str,
        old_ids: list[str],
        messages: list[dict],
    ) -> None:
        """Atomically ACK old entries and re-enqueue as new (under one lock)."""
        async with self._lock:
            # Remove old entries from pending
            for mid in old_ids:
                key = self._msg_id_index.pop(mid, None)
                if key is None:
                    continue
                tid, name, token = key
                if tid != tenant_id or name != to_name:
                    continue
                entry = self._pending.get(key)
                if entry:
                    msgs, fetch_time = entry
                    msgs[:] = [m for m in msgs if m.get("id") != mid]
                    if not msgs:
                        self._pending.pop(key, None)
            # Re-enqueue as fresh messages
            self._queues[(tenant_id, to_name)].extend(messages)

    async def pending_count(
        self, tenant_id: str, to_name: str
    ) -> int:
        async with self._lock:
            total = 0
            for (tid, name, _), (msgs, _) in self._pending.items():
                if tid == tenant_id and name == to_name:
                    total += len(msgs)
            return total

    async def peek(self, tenant_id: str, to_name: str) -> int:
        async with self._lock:
            return len(self._queues[(tenant_id, to_name)])

    async def count_all(self, tenant_id: str) -> int:
        async with self._lock:
            total = 0
            for (tid, _), msgs in self._queues.items():
                if tid == tenant_id:
                    total += len(msgs)
            return total

    async def count_all_global(self) -> int:
        async with self._lock:
            return sum(len(msgs) for msgs in self._queues.values())

    async def recipient_depth(self, tenant_id: str, to_name: str) -> int:
        async with self._lock:
            key = (tenant_id, to_name)
            queued = len(self._queues.get(key, []))
            pending = 0
            for (tid, name, _), (msgs, _) in self._pending.items():
                if tid == tenant_id and name == to_name:
                    pending += len(msgs)
            return queued + pending

    def clear(self) -> None:
        self._queues.clear()
        self._pending.clear()
        self._msg_id_index.clear()


# -- Rooms ------------------------------------------------------------------


class InMemoryRoomBackend:
    """Room CRUD with a secondary name-index for fast lookup by name."""

    def __init__(self) -> None:
        # (tenant_id, room_id) -> room_data dict
        self._rooms: dict[tuple[str, str], dict] = {}
        # (tenant_id, name) -> room_id  (secondary index)
        self._name_index: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def create(
        self, tenant_id: str, room_id: str, room_data: dict
    ) -> None:
        async with self._lock:
            self._rooms[(tenant_id, room_id)] = room_data
            name = room_data.get("name")
            if name:
                self._name_index[(tenant_id, name)] = room_id

    async def get(self, tenant_id: str, room_id: str) -> dict | None:
        async with self._lock:
            data = self._rooms.get((tenant_id, room_id))
            return dict(data) if data is not None else None

    async def get_by_name(
        self, tenant_id: str, name: str
    ) -> tuple[str, dict] | None:
        async with self._lock:
            room_id = self._name_index.get((tenant_id, name))
            if room_id is None:
                return None
            data = self._rooms.get((tenant_id, room_id))
            if data is None:
                return None
            return room_id, dict(data)

    async def list_all(self, tenant_id: str) -> list[tuple[str, dict]]:
        async with self._lock:
            return [
                (rid, dict(data))
                for (tid, rid), data in self._rooms.items()
                if tid == tenant_id
            ]

    async def list_by_member(
        self, tenant_id: str, member_name: str
    ) -> list[tuple[str, dict]]:
        async with self._lock:
            return [
                (rid, dict(data))
                for (tid, rid), data in self._rooms.items()
                if tid == tenant_id and member_name in data.get("members", {})
            ]

    async def update(
        self, tenant_id: str, room_id: str, updates: dict
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            if key not in self._rooms:
                return
            old_name = self._rooms[key].get("name")
            self._rooms[key].update(updates)
            new_name = self._rooms[key].get("name")
            # Maintain the name index if the name changed.
            if old_name != new_name:
                if old_name:
                    self._name_index.pop((tenant_id, old_name), None)
                if new_name:
                    self._name_index[(tenant_id, new_name)] = room_id

    async def delete(self, tenant_id: str, room_id: str) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            data = self._rooms.pop(key, None)
            if data is not None:
                name = data.get("name")
                if name:
                    self._name_index.pop((tenant_id, name), None)

    async def create_if_name_available(
        self, tenant_id: str, room_id: str, room_data: dict
    ) -> bool:
        async with self._lock:
            name = room_data.get("name")
            if name and (tenant_id, name) in self._name_index:
                return False
            self._rooms[(tenant_id, room_id)] = room_data
            if name:
                self._name_index[(tenant_id, name)] = room_id
            return True

    async def rename_if_available(
        self, tenant_id: str, room_id: str, new_name: str
    ) -> bool:
        async with self._lock:
            key = (tenant_id, room_id)
            if key not in self._rooms:
                return False
            # Check if new name is taken by a different room
            existing_id = self._name_index.get((tenant_id, new_name))
            if existing_id is not None and existing_id != room_id:
                return False
            old_name = self._rooms[key].get("name")
            if old_name:
                self._name_index.pop((tenant_id, old_name), None)
            self._rooms[key]["name"] = new_name
            self._name_index[(tenant_id, new_name)] = room_id
            return True

    async def add_member(
        self, tenant_id: str, room_id: str, name: str, role: str
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            if key not in self._rooms:
                return
            members = self._rooms[key].setdefault("members", {})
            members[name] = role

    async def add_member_if_capacity(
        self, tenant_id: str, room_id: str, name: str, role: str, max_members: int
    ) -> bool:
        async with self._lock:
            key = (tenant_id, room_id)
            if key not in self._rooms:
                return False
            members = self._rooms[key].setdefault("members", {})
            if name not in members and len(members) >= max_members:
                return False
            members[name] = role
            return True

    async def remove_member(
        self, tenant_id: str, room_id: str, name: str
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            if key not in self._rooms:
                return
            self._rooms[key].get("members", {}).pop(name, None)

    async def get_members(
        self, tenant_id: str, room_id: str
    ) -> dict[str, str]:
        async with self._lock:
            key = (tenant_id, room_id)
            if key not in self._rooms:
                return {}
            return dict(self._rooms[key].get("members", {}))

    async def count(self, tenant_id: str) -> int:
        async with self._lock:
            return sum(1 for (tid, _) in self._rooms if tid == tenant_id)

    async def count_global(self) -> int:
        async with self._lock:
            return len(self._rooms)

    def clear(self) -> None:
        self._rooms.clear()
        self._name_index.clear()


# -- Room history -----------------------------------------------------------


class InMemoryRoomHistoryBackend:
    """Append-only room history with automatic trimming."""

    def __init__(self, max_history: int = 200) -> None:
        self._history: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._max_history = max_history
        self._lock = asyncio.Lock()

    async def append(
        self, tenant_id: str, room_id: str, message: dict
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            self._history[key].append(message)
            if len(self._history[key]) > self._max_history:
                self._history[key] = self._history[key][-self._max_history :]

    async def get_recent(
        self, tenant_id: str, room_id: str, limit: int
    ) -> list[dict]:
        async with self._lock:
            return list(self._history[(tenant_id, room_id)][-limit:])

    async def search(
        self,
        tenant_id: str,
        room_id: str,
        q: str = "",
        sender: str = "",
        message_type: str = "",
        limit: int = 50,
    ) -> list[dict]:
        async with self._lock:
            results: list[dict] = []
            q_lower = q.lower()
            for msg in reversed(self._history[(tenant_id, room_id)]):
                if q and q_lower not in msg.get("content", "").lower():
                    continue
                if sender and msg.get("from") != sender:
                    continue
                if message_type and msg.get("message_type") != message_type:
                    continue
                results.append(msg)
                if len(results) >= limit:
                    break
            results.reverse()
            return results

    async def get_by_id(
        self, tenant_id: str, room_id: str, message_id: str
    ) -> dict | None:
        async with self._lock:
            for msg in self._history[(tenant_id, room_id)]:
                if msg.get("id") == message_id:
                    return dict(msg)
            return None

    async def get_thread(
        self, tenant_id: str, room_id: str, parent_id: str, limit: int = 50
    ) -> list[dict]:
        async with self._lock:
            parent = None
            replies = []
            for m in self._history[(tenant_id, room_id)]:
                if m.get("id") == parent_id:
                    parent = dict(m)
                elif m.get("reply_to") == parent_id:
                    replies.append(dict(m))
            result = ([parent] if parent else []) + replies
            return result[-limit:]

    async def delete(self, tenant_id: str, room_id: str) -> None:
        async with self._lock:
            self._history.pop((tenant_id, room_id), None)

    async def rename_room_in_history(
        self, tenant_id: str, room_id: str, new_name: str
    ) -> None:
        async with self._lock:
            for msg in self._history.get((tenant_id, room_id), []):
                msg["room"] = new_name

    def clear(self) -> None:
        self._history.clear()


# -- Presence ---------------------------------------------------------------


class InMemoryPresenceBackend:
    """Agent heartbeat tracking."""

    def __init__(self) -> None:
        # (tenant_id, name) -> presence entry
        self._entries: dict[tuple[str, str], dict] = {}
        self._lock = asyncio.Lock()

    async def heartbeat(
        self, tenant_id: str, name: str, status: str, room: str
    ) -> dict:
        now = time.time()
        async with self._lock:
            key = (tenant_id, name)
            entry = self._entries.get(key)
            if entry is None:
                entry = {
                    "name": name,
                    "status": status,
                    "room": room,
                    "last_heartbeat": now,
                    "uptime_start": now,
                }
            else:
                entry["status"] = status
                entry["room"] = room
                entry["last_heartbeat"] = now
            self._entries[key] = entry
            return dict(entry)

    async def list_all(
        self, tenant_id: str, timeout_seconds: int
    ) -> list[dict]:
        now = time.time()
        cutoff = now - timeout_seconds
        async with self._lock:
            return [
                dict(e)
                for (tid, _), e in self._entries.items()
                if tid == tenant_id and e["last_heartbeat"] >= cutoff
            ]

    def clear(self) -> None:
        self._entries.clear()


# -- Rate limiting ----------------------------------------------------------


class InMemoryRateLimitBackend:
    """Sliding-window rate limiter using timestamp lists."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check_and_increment(
        self, tenant_id: str, sender: str, window: int, max_count: int
    ) -> bool:
        """Return ``True`` if the request is allowed."""
        now = time.time()
        cutoff = now - window
        async with self._lock:
            key = (tenant_id, sender)
            timestamps = self._buckets[key]
            # Prune expired entries
            self._buckets[key] = [t for t in timestamps if t > cutoff]
            if len(self._buckets[key]) >= max_count:
                return False
            self._buckets[key].append(now)
            return True

    def clear(self) -> None:
        self._buckets.clear()


# -- SSE tokens -------------------------------------------------------------


class InMemorySSETokenBackend:
    """Short-lived tokens for authenticating SSE connections."""

    def __init__(self) -> None:
        # token_str -> {recipient, tenant_id, expires}
        self._tokens: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def create_token(
        self, tenant_id: str, recipient: str, ttl: int
    ) -> str:
        token = uuid.uuid4().hex
        async with self._lock:
            self._tokens[token] = {
                "recipient": recipient,
                "tenant_id": tenant_id,
                "expires": time.time() + ttl,
            }
        return token

    async def verify_token(
        self, token: str, recipient: str
    ) -> tuple[bool, str]:
        """Return ``(valid, tenant_id)``.  Consumes the token on success."""
        async with self._lock:
            entry = self._tokens.pop(token, None)
            if entry is None:
                return False, ""
            if time.time() > entry["expires"]:
                return False, ""
            if entry["recipient"] != recipient:
                return False, ""
            return True, entry["tenant_id"]

    def clear(self) -> None:
        self._tokens.clear()


# -- Webhooks (DM + room) ---------------------------------------------------


class InMemoryWebhookBackend:
    """DM and room webhook registrations with per-webhook secrets."""

    def __init__(self) -> None:
        # (tenant_id, instance_name) -> {"url": ..., "secret": ...}
        self._dm_hooks: dict[tuple[str, str], dict] = {}
        # (tenant_id, room_id) -> [{"url", "secret", "registered_by"}]
        self._room_hooks: dict[tuple[str, str], list[dict]] = defaultdict(
            list
        )
        self._lock = asyncio.Lock()

    async def register_dm(
        self,
        tenant_id: str,
        instance_name: str,
        callback_url: str,
        secret: str = "",
    ) -> None:
        async with self._lock:
            self._dm_hooks[(tenant_id, instance_name)] = {
                "url": callback_url,
                "secret": secret,
            }

    async def get_dm(
        self, tenant_id: str, instance_name: str
    ) -> dict | None:
        async with self._lock:
            return self._dm_hooks.get((tenant_id, instance_name))

    async def delete_dm(
        self, tenant_id: str, instance_name: str
    ) -> None:
        async with self._lock:
            self._dm_hooks.pop((tenant_id, instance_name), None)

    async def register_room(
        self,
        tenant_id: str,
        room_id: str,
        callback_url: str,
        registered_by: str,
        secret: str = "",
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            # Prevent duplicate URLs for the same room.
            for hook in self._room_hooks[key]:
                if hook["url"] == callback_url:
                    hook["registered_by"] = registered_by
                    hook["secret"] = secret
                    return
            self._room_hooks[key].append(
                {"url": callback_url, "registered_by": registered_by, "secret": secret}
            )

    async def list_room(
        self, tenant_id: str, room_id: str
    ) -> list[dict]:
        async with self._lock:
            return list(self._room_hooks[(tenant_id, room_id)])

    async def delete_room(
        self, tenant_id: str, room_id: str, callback_url: str
    ) -> bool:
        async with self._lock:
            key = (tenant_id, room_id)
            hooks = self._room_hooks[key]
            before = len(hooks)
            self._room_hooks[key] = [
                h for h in hooks if h["url"] != callback_url
            ]
            return len(self._room_hooks[key]) < before

    def clear(self) -> None:
        self._dm_hooks.clear()
        self._room_hooks.clear()


# -- Analytics --------------------------------------------------------------


class InMemoryAnalyticsBackend:
    """Per-tenant send/delivery counters."""

    def __init__(self) -> None:
        self._stats: dict[str, dict] = defaultdict(
            lambda: {
                "total_sent": 0,
                "total_delivered": 0,
                "per_sender": defaultdict(int),
                "per_recipient": defaultdict(int),
                "hourly_volume": defaultdict(int),
            }
        )
        self._lock = asyncio.Lock()

    async def track_send(self, tenant_id: str, sender: str) -> None:
        from datetime import datetime, timezone

        async with self._lock:
            stats = self._stats[tenant_id]
            stats["total_sent"] += 1
            stats["per_sender"][sender] += 1
            hour_key = (
                datetime.now(timezone.utc)
                .replace(minute=0, second=0, microsecond=0)
                .isoformat()
            )
            stats["hourly_volume"][hour_key] += 1

    async def track_delivery(
        self, tenant_id: str, recipient: str, count: int
    ) -> None:
        async with self._lock:
            stats = self._stats[tenant_id]
            stats["total_delivered"] += count
            stats["per_recipient"][recipient] += count

    async def get_stats(self, tenant_id: str) -> dict:
        async with self._lock:
            stats = self._stats[tenant_id]
            return {
                "total_sent": stats["total_sent"],
                "total_delivered": stats["total_delivered"],
                "per_sender": dict(stats["per_sender"]),
                "per_recipient": dict(stats["per_recipient"]),
                "hourly_volume": dict(stats["hourly_volume"]),
            }

    def clear(self) -> None:
        self._stats.clear()


# -- Participants -----------------------------------------------------------


class InMemoryParticipantBackend:
    """Track known participant names in memory."""

    def __init__(self) -> None:
        # (tenant_id,) -> set of names
        self._participants: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def add(self, tenant_id: str, *names: str) -> None:
        async with self._lock:
            self._participants[tenant_id].update(names)

    async def list_all(self, tenant_id: str) -> list[str]:
        async with self._lock:
            return sorted(self._participants.get(tenant_id, set()))

    async def count_global(self) -> int:
        async with self._lock:
            return sum(len(s) for s in self._participants.values())

    def clear(self) -> None:
        self._participants.clear()


# -- Idempotency ------------------------------------------------------------


class InMemoryIdempotencyBackend:
    """Idempotency key storage with TTL-based expiration."""

    def __init__(self) -> None:
        # (tenant_id, key) -> (result, expiry_time)
        self._store: dict[tuple[str, str], tuple[dict, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, tenant_id: str, key: str) -> dict | None:
        async with self._lock:
            now = time.time()
            cache_key = (tenant_id, key)
            if cache_key in self._store:
                cached_result, expiry = self._store[cache_key]
                if now < expiry:
                    return cached_result
                # Expired, remove
                del self._store[cache_key]
            return None

    async def set(
        self, tenant_id: str, key: str, result: dict, ttl: int
    ) -> None:
        async with self._lock:
            now = time.time()
            self._store[(tenant_id, key)] = (result, now + ttl)
            # Lazy cleanup of expired entries
            expired = [k for k, (_, exp) in self._store.items() if now >= exp]
            for k in expired:
                del self._store[k]

    def clear(self) -> None:
        self._store.clear()


# -- Webhook delivery queue -------------------------------------------------


_MEMORY_BACKOFF_BASE = 2  # seconds


class InMemoryWebhookQueueBackend:
    """In-memory webhook delivery queue with retry and DLQ support."""

    def __init__(self, dlq_max_size: int = 1000) -> None:
        self._queue: list[tuple[str, dict]] = []  # (job_id, job_data)
        self._delayed: list[tuple[float, dict]] = []  # (retry_at, job_data)
        self._pending: dict[str, dict] = {}  # job_id -> job_data
        self._dlq: list[dict] = []
        self._dlq_max_size = dlq_max_size
        self._counter = 0
        self._lock = asyncio.Lock()

    async def enqueue(self, job: dict) -> str:
        """Add a webhook job to the queue. Returns job ID."""
        async with self._lock:
            self._counter += 1
            job_id = f"job-{self._counter}"
            self._queue.append((job_id, job))
            return job_id

    async def fetch(self, count: int = 10) -> list[tuple[str, dict]]:
        """Fetch pending jobs for delivery."""
        async with self._lock:
            # Promote due delayed jobs first
            self._promote_delayed_locked()
            fetched = self._queue[:count]
            self._queue = self._queue[count:]
            for job_id, job in fetched:
                self._pending[job_id] = job
            return fetched

    def _promote_delayed_locked(self) -> int:
        """Move due delayed jobs to main queue (call under lock)."""
        now = time.time()
        promoted = 0
        still_delayed = []
        for retry_at, job in self._delayed:
            if retry_at <= now:
                self._counter += 1
                job_id = f"job-{self._counter}"
                self._queue.append((job_id, job))
                promoted += 1
            else:
                still_delayed.append((retry_at, job))
        self._delayed = still_delayed
        return promoted

    async def promote_delayed(self, max_promote: int = 100) -> int:
        """Move due delayed jobs back to the main queue."""
        async with self._lock:
            return self._promote_delayed_locked()

    async def ack(self, job_id: str) -> None:
        """Acknowledge successful delivery, removing the job."""
        async with self._lock:
            self._pending.pop(job_id, None)

    async def nack(
        self, job_id: str, error: str, max_retries: int
    ) -> bool:
        """Mark job as failed. Returns True if job will be retried."""
        async with self._lock:
            job = self._pending.pop(job_id, None)
            if job is None:
                return False

            job["attempt"] = job.get("attempt", 0) + 1
            job["last_error"] = error

            if job["attempt"] >= max_retries:
                # Move to DLQ
                self._dlq.insert(0, job)
                if len(self._dlq) > self._dlq_max_size:
                    self._dlq = self._dlq[: self._dlq_max_size]
                return False

            # Schedule for delayed retry with exponential backoff
            delay_seconds = _MEMORY_BACKOFF_BASE ** job["attempt"]
            retry_at = time.time() + delay_seconds
            self._delayed.append((retry_at, job))
            return True

    async def get_dlq(self, limit: int = 50) -> list[dict]:
        """Return recent dead letter queue entries."""
        async with self._lock:
            return list(self._dlq[:limit])

    async def get_stats(self) -> dict:
        """Return queue statistics."""
        async with self._lock:
            return {
                "queue_length": len(self._queue),
                "pending": len(self._pending),
                "delayed": len(self._delayed),
                "dlq_size": len(self._dlq),
            }

    def clear(self) -> None:
        self._queue.clear()
        self._delayed.clear()
        self._pending.clear()
        self._dlq.clear()



class InMemoryRoomStateBackend:
    """Per-room coordination state: goal, claimed tasks, file locks, decisions."""

    def __init__(self) -> None:
        # (tenant_id, room_id) -> state dict
        self._state: dict[tuple[str, str], dict] = {}
        self._lock = asyncio.Lock()

    def _empty(self) -> dict:
        return {
            "active_goal": None,
            "claimed_tasks": [],
            "locked_files": {},
            "resolved_decisions": [],
        }

    async def get(self, tenant_id: str, room_id: str) -> dict:
        async with self._lock:
            return dict(self._state.get((tenant_id, room_id), self._empty()))

    async def set_goal(
        self, tenant_id: str, room_id: str, goal: str | None
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            self._state.setdefault(key, self._empty())
            self._state[key]["active_goal"] = goal

    async def add_claimed_task(
        self, tenant_id: str, room_id: str, task: dict
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            self._state.setdefault(key, self._empty())
            self._state[key]["claimed_tasks"].append(task)
            # Also write the file lock entry
            fp = task.get("file_path")
            if fp:
                self._state[key]["locked_files"][fp] = {
                    "held_by": task["claimed_by"],
                    "lock_token": task["lock_token"],
                    "expires_at": task["expires_at"],
                }

    async def remove_claimed_task(
        self, tenant_id: str, room_id: str, task_id: str
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            if key not in self._state:
                return
            tasks = self._state[key]["claimed_tasks"]
            removed = None
            self._state[key]["claimed_tasks"] = [
                t for t in tasks if t.get("id") != task_id
                or (removed := t) is None  # capture and drop
            ]
            if removed:
                fp = removed.get("file_path")
                if fp:
                    self._state[key]["locked_files"].pop(fp, None)

    async def set_lock(
        self, tenant_id: str, room_id: str, file_path: str, lock_data: dict
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            self._state.setdefault(key, self._empty())
            self._state[key]["locked_files"][file_path] = lock_data

    async def release_lock(
        self, tenant_id: str, room_id: str, file_path: str
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            if key in self._state:
                self._state[key]["locked_files"].pop(file_path, None)

    async def add_decision(
        self, tenant_id: str, room_id: str, decision: dict
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            self._state.setdefault(key, self._empty())
            self._state[key]["resolved_decisions"].append(decision)

    async def expire_tasks(
        self, tenant_id: str, room_id: str
    ) -> list[str]:
        now = time.time()
        async with self._lock:
            key = (tenant_id, room_id)
            if key not in self._state:
                return []
            expired_ids: list[str] = []
            live_tasks: list[dict] = []
            for task in self._state[key]["claimed_tasks"]:
                # expires_at is ISO8601; parse to epoch for comparison
                try:
                    from datetime import datetime
                    exp = datetime.fromisoformat(
                        task["expires_at"]
                    ).timestamp()
                    if exp < now:
                        expired_ids.append(task["id"])
                        fp = task.get("file_path")
                        if fp:
                            self._state[key]["locked_files"].pop(fp, None)
                    else:
                        live_tasks.append(task)
                except (KeyError, ValueError):
                    live_tasks.append(task)
            self._state[key]["claimed_tasks"] = live_tasks
            return expired_ids

    def clear(self) -> None:
        self._state.clear()




# -- Convenience bundle -----------------------------------------------------


@dataclass
class InMemoryBackends:
    """Holds all in-memory backend instances for easy wiring."""

    messages: InMemoryMessageBackend
    rooms: InMemoryRoomBackend
    room_history: InMemoryRoomHistoryBackend
    presence: InMemoryPresenceBackend
    rate_limit: InMemoryRateLimitBackend
    sse_tokens: InMemorySSETokenBackend
    webhooks: InMemoryWebhookBackend
    analytics: InMemoryAnalyticsBackend
    participants: InMemoryParticipantBackend = field(
        default_factory=InMemoryParticipantBackend
    )
    idempotency: InMemoryIdempotencyBackend = field(
        default_factory=InMemoryIdempotencyBackend
    )
    webhook_queue: InMemoryWebhookQueueBackend = field(
        default_factory=InMemoryWebhookQueueBackend
    )
    room_state: InMemoryRoomStateBackend = field(
        default_factory=InMemoryRoomStateBackend
    )

    @classmethod
    def create(cls, max_room_history: int = 200) -> InMemoryBackends:
        """Factory that instantiates every backend with sensible defaults."""
        return cls(
            messages=InMemoryMessageBackend(),
            rooms=InMemoryRoomBackend(),
            room_history=InMemoryRoomHistoryBackend(
                max_history=max_room_history
            ),
            presence=InMemoryPresenceBackend(),
            rate_limit=InMemoryRateLimitBackend(),
            sse_tokens=InMemorySSETokenBackend(),
            webhooks=InMemoryWebhookBackend(),
            analytics=InMemoryAnalyticsBackend(),
            participants=InMemoryParticipantBackend(),
            idempotency=InMemoryIdempotencyBackend(),
            webhook_queue=InMemoryWebhookQueueBackend(),
            room_state=InMemoryRoomStateBackend(),
        )

    def clear_all(self) -> None:
        """Reset every backend — useful in test teardown."""
        self.messages.clear()
        self.rooms.clear()
        self.room_history.clear()
        self.presence.clear()
        self.rate_limit.clear()
        self.sse_tokens.clear()
        self.webhooks.clear()
        self.analytics.clear()
        self.participants.clear()
        self.idempotency.clear()
        self.webhook_queue.clear()
        self.room_state.clear()
