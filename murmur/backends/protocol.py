"""Protocol definitions for Murmur relay backends.

Each protocol describes one responsibility of the relay.  Implementations may
be in-memory (for dev/test), Redis-backed (for single-node prod), or
Postgres-backed (for multi-node prod).  All methods are async.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Messages (DM inboxes)
# ---------------------------------------------------------------------------


@runtime_checkable
class MessageBackend(Protocol):
    """Per-recipient message queue (DM inbox)."""

    async def enqueue(self, tenant_id: str, to_name: str, message: dict) -> None:
        """Append one message to a recipient's inbox."""
        ...

    async def enqueue_batch(
        self, tenant_id: str, to_name: str, messages: list[dict]
    ) -> None:
        """Append multiple messages atomically."""
        ...

    async def dequeue_all(self, tenant_id: str, to_name: str) -> list[dict]:
        """Pop and return all messages, clearing the inbox."""
        ...

    async def peek(self, tenant_id: str, to_name: str) -> int:
        """Return the number of pending messages without consuming them."""
        ...


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------


@runtime_checkable
class RoomBackend(Protocol):
    """Room CRUD and membership management."""

    async def create(self, tenant_id: str, room_id: str, room_data: dict) -> None:
        ...

    async def get(self, tenant_id: str, room_id: str) -> dict | None:
        ...

    async def get_by_name(
        self, tenant_id: str, name: str
    ) -> tuple[str, dict] | None:
        """Look up a room by its human-readable name.  Returns (room_id, data)."""
        ...

    async def list_all(self, tenant_id: str) -> list[tuple[str, dict]]:
        """Return all rooms for a tenant as (room_id, data) pairs."""
        ...

    async def update(
        self, tenant_id: str, room_id: str, updates: dict
    ) -> None:
        ...

    async def delete(self, tenant_id: str, room_id: str) -> None:
        ...

    async def add_member(
        self, tenant_id: str, room_id: str, name: str, role: str
    ) -> None:
        ...

    async def remove_member(
        self, tenant_id: str, room_id: str, name: str
    ) -> None:
        ...

    async def get_members(
        self, tenant_id: str, room_id: str
    ) -> dict[str, str]:
        """Return ``{name: role}`` for all members of a room."""
        ...


# ---------------------------------------------------------------------------
# Room history
# ---------------------------------------------------------------------------


@runtime_checkable
class RoomHistoryBackend(Protocol):
    """Append-only room message history (not cleared on read)."""

    async def append(
        self, tenant_id: str, room_id: str, message: dict
    ) -> None:
        ...

    async def get_recent(
        self, tenant_id: str, room_id: str, limit: int
    ) -> list[dict]:
        ...

    async def search(
        self,
        tenant_id: str,
        room_id: str,
        q: str = "",
        sender: str = "",
        message_type: str = "",
        limit: int = 50,
    ) -> list[dict]:
        ...


# ---------------------------------------------------------------------------
# Presence / heartbeat
# ---------------------------------------------------------------------------


@runtime_checkable
class PresenceBackend(Protocol):
    async def heartbeat(
        self, tenant_id: str, name: str, status: str, room: str
    ) -> dict:
        """Record a heartbeat and return the stored presence entry."""
        ...

    async def list_all(
        self, tenant_id: str, timeout_seconds: int
    ) -> list[dict]:
        """Return all participants whose last heartbeat is within *timeout_seconds*."""
        ...


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@runtime_checkable
class RateLimitBackend(Protocol):
    async def check_and_increment(
        self, tenant_id: str, sender: str, window: int, max_count: int
    ) -> bool:
        """Return ``True`` if the request is allowed, ``False`` if rate-limited."""
        ...


# ---------------------------------------------------------------------------
# SSE tokens
# ---------------------------------------------------------------------------


@runtime_checkable
class SSETokenBackend(Protocol):
    async def create_token(
        self, tenant_id: str, recipient: str, ttl: int
    ) -> str:
        """Create a short-lived token for SSE connections."""
        ...

    async def verify_token(
        self, token: str, recipient: str
    ) -> tuple[bool, str]:
        """Return ``(valid, tenant_id)``."""
        ...


# ---------------------------------------------------------------------------
# Webhooks (DM + room)
# ---------------------------------------------------------------------------


@runtime_checkable
class WebhookBackend(Protocol):
    async def register_dm(
        self, tenant_id: str, instance_name: str, callback_url: str
    ) -> None:
        ...

    async def get_dm(
        self, tenant_id: str, instance_name: str
    ) -> str | None:
        ...

    async def delete_dm(
        self, tenant_id: str, instance_name: str
    ) -> None:
        ...

    async def register_room(
        self,
        tenant_id: str,
        room_id: str,
        callback_url: str,
        registered_by: str,
    ) -> None:
        ...

    async def list_room(
        self, tenant_id: str, room_id: str
    ) -> list[dict]:
        ...

    async def delete_room(
        self, tenant_id: str, room_id: str, callback_url: str
    ) -> bool:
        ...


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@runtime_checkable
class AnalyticsBackend(Protocol):
    async def track_send(self, tenant_id: str, sender: str) -> None:
        ...

    async def track_delivery(
        self, tenant_id: str, recipient: str, count: int
    ) -> None:
        ...

    async def get_stats(self, tenant_id: str) -> dict:
        ...
