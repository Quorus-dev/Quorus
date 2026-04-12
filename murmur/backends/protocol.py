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

    async def enqueue_fanout(
        self, tenant_id: str, messages_by_recipient: dict[str, dict]
    ) -> None:
        """Fan-out: enqueue one message per recipient in a single operation.

        This is optimized for room messaging where the same message goes
        to many recipients. Implementations should pipeline writes to
        minimize round-trips.

        Args:
            messages_by_recipient: dict mapping recipient name -> message dict
        """
        ...

    async def dequeue_all(self, tenant_id: str, to_name: str) -> list[dict]:
        """Pop and return all messages, clearing the inbox.

        .. deprecated:: Use :meth:`fetch` + :meth:`ack` for crash-safe delivery.
        """
        ...

    async def fetch(
        self, tenant_id: str, to_name: str
    ) -> tuple[list[dict], str]:
        """Fetch pending messages with at-least-once delivery semantics.

        Returns ``(messages, ack_token)``.  Messages become *pending* until
        :meth:`ack` is called.  Unacknowledged messages are redelivered
        on the next fetch after the visibility timeout expires.

        The ack_token is opaque — it may be a single ID, a JSON-encoded
        list of stream entry IDs, or any backend-specific token.
        """
        ...

    async def ack(
        self, tenant_id: str, to_name: str, ack_token: str
    ) -> None:
        """Acknowledge receipt of a previous :meth:`fetch`, permanently
        removing the pending messages.  Unacked messages are redelivered."""
        ...

    async def ack_ids(
        self, tenant_id: str, to_name: str, message_ids: list[str]
    ) -> int:
        """Acknowledge specific message IDs.  Returns the count of newly acked.

        This enables client-side ACK where the client confirms individual
        messages after processing them.
        """
        ...

    async def requeue(
        self,
        tenant_id: str,
        to_name: str,
        old_ids: list[str],
        messages: list[dict],
    ) -> None:
        """Atomically ACK *old_ids* and re-enqueue *messages* as new entries.

        Used to return incomplete chunk groups to the inbox without a
        crash window.  If the process dies mid-operation, either the old
        entries survive (will be reclaimed by visibility timeout) or the
        new entries survive — no data is lost.
        """
        ...

    async def pending_count(
        self, tenant_id: str, to_name: str
    ) -> int:
        """Count messages that have been delivered but not yet acknowledged."""
        ...

    async def peek(self, tenant_id: str, to_name: str) -> int:
        """Return the number of pending messages without consuming them."""
        ...

    async def count_all(self, tenant_id: str) -> int:
        """Count total pending messages across all recipients for a tenant."""
        ...

    async def count_all_global(self) -> int:
        """Count total pending messages globally (for health checks)."""
        ...

    async def recipient_depth(self, tenant_id: str, to_name: str) -> int:
        """Return total stream length for a recipient (queued + pending).

        Used for quota enforcement to prevent unbounded queue growth.
        """
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

    async def list_by_member(
        self, tenant_id: str, member_name: str
    ) -> list[tuple[str, dict]]:
        """Return rooms where *member_name* is a member, as (room_id, data) pairs."""
        ...

    async def update(
        self, tenant_id: str, room_id: str, updates: dict
    ) -> None:
        ...

    async def delete(self, tenant_id: str, room_id: str) -> None:
        ...

    async def create_if_name_available(
        self, tenant_id: str, room_id: str, room_data: dict
    ) -> bool:
        """Atomically create a room only if the name is not taken.

        Returns True if created, False if name already exists.
        """
        ...

    async def rename_if_available(
        self, tenant_id: str, room_id: str, new_name: str
    ) -> bool:
        """Atomically rename a room only if the new name is not taken.

        Returns True if renamed, False if name already exists.
        """
        ...

    async def add_member(
        self, tenant_id: str, room_id: str, name: str, role: str
    ) -> None:
        ...

    async def add_member_if_capacity(
        self, tenant_id: str, room_id: str, name: str, role: str, max_members: int
    ) -> bool:
        """Atomically add a member only if the room is under capacity.

        Returns True if added, False if at max capacity.
        """
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

    async def count(self, tenant_id: str) -> int:
        """Count rooms in a tenant."""
        ...

    async def count_global(self) -> int:
        """Count all rooms (for health checks)."""
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

    async def delete(self, tenant_id: str, room_id: str) -> None:
        """Delete history for a room."""
        ...

    async def rename_room_in_history(
        self, tenant_id: str, room_id: str, new_name: str
    ) -> None:
        """Update room name in all history messages."""
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
        self,
        tenant_id: str,
        instance_name: str,
        callback_url: str,
        secret: str = "",
    ) -> None:
        """Register a DM webhook. Optional per-webhook secret for signing."""
        ...

    async def get_dm(
        self, tenant_id: str, instance_name: str
    ) -> dict | None:
        """Return ``{"url": ..., "secret": ...}`` or None."""
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
        secret: str = "",
    ) -> None:
        """Register a room webhook. Optional per-webhook secret for signing."""
        ...

    async def list_room(
        self, tenant_id: str, room_id: str
    ) -> list[dict]:
        """Return list of ``{"url": ..., "secret": ..., "registered_by": ...}``."""
        ...

    async def delete_room(
        self, tenant_id: str, room_id: str, callback_url: str
    ) -> bool:
        ...


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@runtime_checkable
class ParticipantBackend(Protocol):
    """Track known participant names."""

    async def add(self, tenant_id: str, *names: str) -> None:
        ...

    async def list_all(self, tenant_id: str) -> list[str]:
        ...

    async def count_global(self) -> int:
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


# ---------------------------------------------------------------------------
# Webhook delivery queue
# ---------------------------------------------------------------------------


@runtime_checkable
class WebhookQueueBackend(Protocol):
    """Durable webhook delivery queue with retry and DLQ support.

    Uses Redis Streams for at-least-once delivery of webhook jobs.
    Jobs that fail after max retries are moved to a dead letter queue.
    Retries use exponential backoff (2s, 4s, 8s, etc.).
    """

    async def enqueue(self, job: dict) -> str:
        """Add a webhook job to the queue. Returns job ID."""
        ...

    async def fetch(self, count: int = 10) -> list[tuple[str, dict]]:
        """Fetch pending jobs for delivery.

        Returns list of (job_id, job_data) tuples.
        Jobs become pending until ack() or nack() is called.
        Automatically promotes due delayed jobs before fetching.
        """
        ...

    async def ack(self, job_id: str) -> None:
        """Acknowledge successful delivery, removing the job."""
        ...

    async def nack(
        self, job_id: str, error: str, max_retries: int
    ) -> bool:
        """Mark job as failed. Returns True if job will be retried.

        If attempts >= max_retries, job is moved to DLQ and False is returned.
        Otherwise, job is scheduled for retry after exponential backoff delay.
        """
        ...

    async def promote_delayed(self, max_promote: int = 100) -> int:
        """Move due delayed jobs back to the main queue.

        Called automatically by fetch(), but can be called separately.
        Returns the number of jobs promoted.
        """
        ...

    async def get_dlq(self, limit: int = 50) -> list[dict]:
        """Return recent dead letter queue entries for monitoring."""
        ...

    async def get_stats(self) -> dict:
        """Return queue statistics: pending, delayed, dlq_size, etc."""
        ...


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@runtime_checkable
class IdempotencyBackend(Protocol):
    """Idempotency key storage for deduplicating retried requests.

    Supports atomic reservation to prevent race conditions:
    1. Call reserve(key, body_fingerprint) to atomically claim the key
    2. If reserve() returns None, process the request
    3. Call set(key, body_fingerprint, result) to store the result
    4. If reserve() returns a dict, return the cached result

    Body fingerprinting ensures that reusing an idempotency key with a
    different request body returns 409 Conflict (per RFC).
    """

    async def get(self, tenant_id: str, key: str) -> dict | None:
        """Return cached result for key, or None if not found/expired."""
        ...

    async def reserve(
        self, tenant_id: str, key: str, body_fingerprint: str, ttl: int
    ) -> dict | None:
        """Atomically reserve a key for processing.

        Args:
            tenant_id: Tenant identifier
            key: The idempotency key
            body_fingerprint: SHA256 hash of the request body
            ttl: Time-to-live in seconds

        Returns:
        - None if reservation succeeded (key was free, now reserved)
        - dict with cached result if key already has a completed result

        Raises HTTPException(409) if:
        - Key is reserved but not yet complete (concurrent request)
        - Key exists with a different body fingerprint (key reuse violation)
        """
        ...

    async def set(
        self, tenant_id: str, key: str, body_fingerprint: str, result: dict, ttl: int
    ) -> None:
        """Store result with body fingerprint and TTL."""
        ...

    async def delete(self, tenant_id: str, key: str) -> None:
        """Delete a key (used to release pending reservations on failure)."""
        ...


class RoomStateBackend(Protocol):
    """Shared State Matrix — goal, claimed tasks, file locks, decisions.

    Provides the coordination substrate for Primitive A (read) and
    Primitive B (distributed mutex).  Implementations must be safe for
    concurrent async access.
    """

    async def get(self, tenant_id: str, room_id: str) -> dict:
        """Return a snapshot of the room's coordination state."""
        ...

    async def set_goal(
        self, tenant_id: str, room_id: str, goal: str | None
    ) -> None:
        """Set (or clear) the active goal for a room."""
        ...

    async def add_claimed_task(
        self, tenant_id: str, room_id: str, task: dict
    ) -> None:
        """Record a newly acquired file lock as a claimed task."""
        ...

    async def remove_claimed_task(
        self, tenant_id: str, room_id: str, task_id: str
    ) -> None:
        """Remove a claimed task (on release or manual clear)."""
        ...

    async def set_lock(
        self, tenant_id: str, room_id: str, file_path: str, lock_data: dict
    ) -> None:
        """Directly set a lock entry (used internally by add_claimed_task)."""
        ...

    async def release_lock(
        self, tenant_id: str, room_id: str, file_path: str
    ) -> None:
        """Remove a file lock entry."""
        ...

    async def try_acquire_lock(
        self, tenant_id: str, room_id: str, file_path: str,
        lock_data: dict, task_data: dict
    ) -> tuple[bool, dict | None]:
        """Atomically try to acquire a lock.

        Prevents TOCTOU race conditions where two replicas both read
        "unlocked" and both grant the lock.

        Returns:
            (True, None) if lock was acquired
            (False, existing_lock_data) if lock is already held
        """
        ...

    async def release_lock_atomic(
        self, tenant_id: str, room_id: str, file_path: str, lock_token: str
    ) -> tuple[str, dict | None]:
        """Atomically release a lock with token verification.

        Prevents race conditions where another agent acquires a new lock
        between the token check and the release.

        Returns:
            ("not_found", None) if no lock exists
            ("token_mismatch", existing_lock) if token doesn't match
            ("released", None) if successfully released
        """
        ...

    async def add_decision(
        self, tenant_id: str, room_id: str, decision: dict
    ) -> None:
        """Append a resolved decision to the room's decision log."""
        ...

    async def expire_tasks(
        self, tenant_id: str, room_id: str
    ) -> list[str]:
        """Remove and return IDs of tasks whose TTL has elapsed."""
        ...
