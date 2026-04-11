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
    """Per-recipient DM inbox backed by a plain dict."""

    def __init__(self) -> None:
        self._queues: dict[tuple[str, str], list[dict]] = defaultdict(list)
        # Inflight messages keyed by (tenant_id, to_name, ack_token)
        self._inflight: dict[tuple[str, str, str], list[dict]] = {}
        self._lock = asyncio.Lock()

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
            key = (tenant_id, to_name)
            msgs = list(self._queues[key])
            if not msgs:
                return [], ""
            self._queues[key].clear()
            token = uuid.uuid4().hex
            self._inflight[(tenant_id, to_name, token)] = msgs
            return msgs, token

    async def ack(
        self, tenant_id: str, to_name: str, ack_token: str
    ) -> None:
        async with self._lock:
            self._inflight.pop((tenant_id, to_name, ack_token), None)

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

    def clear(self) -> None:
        self._queues.clear()
        self._inflight.clear()


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

    async def add_member(
        self, tenant_id: str, room_id: str, name: str, role: str
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            if key not in self._rooms:
                return
            members = self._rooms[key].setdefault("members", {})
            members[name] = role

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
        """Return a single message by ID, or None if not found."""
        async with self._lock:
            for msg in self._history[(tenant_id, room_id)]:
                if msg.get("id") == message_id:
                    return dict(msg)
            return None

    async def get_thread(
        self, tenant_id: str, room_id: str, message_id: str
    ) -> list[dict]:
        """Return the parent message and all direct replies."""
        async with self._lock:
            results = []
            for msg in self._history[(tenant_id, room_id)]:
                if msg.get("id") == message_id or msg.get("reply_to") == message_id:
                    results.append(dict(msg))
            return results

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
    """DM and room webhook registrations."""

    def __init__(self) -> None:
        # (tenant_id, instance_name) -> callback_url
        self._dm_hooks: dict[tuple[str, str], str] = {}
        # (tenant_id, room_id) -> [{url, registered_by}]
        self._room_hooks: dict[tuple[str, str], list[dict]] = defaultdict(
            list
        )
        self._lock = asyncio.Lock()

    async def register_dm(
        self, tenant_id: str, instance_name: str, callback_url: str
    ) -> None:
        async with self._lock:
            self._dm_hooks[(tenant_id, instance_name)] = callback_url

    async def get_dm(
        self, tenant_id: str, instance_name: str
    ) -> str | None:
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
    ) -> None:
        async with self._lock:
            key = (tenant_id, room_id)
            # Prevent duplicate URLs for the same room.
            for hook in self._room_hooks[key]:
                if hook["url"] == callback_url:
                    hook["registered_by"] = registered_by
                    return
            self._room_hooks[key].append(
                {"url": callback_url, "registered_by": registered_by}
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
