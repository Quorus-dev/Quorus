"""SSE (Server-Sent Events) service — token management and push delivery.

SSE queues are process-local (connection-scoped), while token storage is
delegated to an SSETokenBackend that may be in-memory or Redis-backed.

Cross-replica push is handled by publishing to :class:`NotificationService`
so that SSE clients connected to *any* replica receive the message.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

import structlog

from murmur.backends.protocol import SSETokenBackend

if TYPE_CHECKING:
    from murmur.services.notification_svc import NotificationService

logger = structlog.get_logger("murmur.services.sse")


class SSEService:
    """Manages SSE authentication tokens and per-recipient push queues."""

    def __init__(
        self,
        backend: SSETokenBackend,
        max_queue_size: int = 1000,
        notification: NotificationService | None = None,
    ) -> None:
        self._backend = backend
        self._max_queue_size = max_queue_size
        self._notification = notification
        # Process-local: (tenant_id, recipient) -> list of asyncio.Queue
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)

    # -- Token management -----------------------------------------------------

    async def create_token(
        self, tenant_id: str, recipient: str, ttl: int
    ) -> str:
        """Create a short-lived token for authenticating an SSE connection."""
        return await self._backend.create_token(tenant_id, recipient, ttl)

    async def verify_token(
        self, token: str, recipient: str
    ) -> tuple[bool, str]:
        """Verify and consume an SSE token.  Returns ``(valid, tenant_id)``."""
        return await self._backend.verify_token(token, recipient)

    # -- Push / queue management (process-local) ------------------------------

    def _queue_key(self, tenant_id: str, recipient: str) -> str:
        return f"{tenant_id}:{recipient}"

    def push(
        self, tenant_id: str, recipient: str, message: dict
    ) -> None:
        """Push *message* to all active SSE connections for *recipient*.

        Also publishes via NotificationService so other replicas can
        deliver to their local SSE queues.
        """
        self._push_local(tenant_id, recipient, message)

        if self._notification:
            from murmur.services.notification_svc import NotificationService

            channel = NotificationService.dm_channel(tenant_id, recipient)
            # Fire-and-forget — schedule the coroutine on the running loop.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._notification.publish(channel, message))
            except RuntimeError:
                pass  # no loop — tests without async context

    def _push_local(
        self, tenant_id: str, recipient: str, message: dict
    ) -> None:
        """Push to process-local SSE queues only (called on Pub/Sub receive)."""
        key = self._queue_key(tenant_id, recipient)
        for q in self._queues.get(key, []):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning(
                    "SSE queue full for %s, dropping message", recipient
                )

    def register_queue(
        self, tenant_id: str, recipient: str
    ) -> asyncio.Queue:
        """Create and register a new SSE queue for a connection.

        If a NotificationService is available, the queue will also receive
        messages published by other replicas via Redis Pub/Sub.
        """
        key = self._queue_key(tenant_id, recipient)
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._queues[key].append(q)

        # Subscribe to cross-replica notifications for this recipient
        if self._notification:
            from murmur.services.notification_svc import NotificationService

            channel = NotificationService.dm_channel(tenant_id, recipient)

            def _on_remote(msg: dict, _q: asyncio.Queue = q) -> None:
                try:
                    _q.put_nowait(msg)
                except asyncio.QueueFull:
                    pass

            # Store handler reference so we can unsubscribe later
            q._remote_handler = _on_remote  # type: ignore[attr-defined]
            q._remote_channel = channel  # type: ignore[attr-defined]
            self._notification.subscribe(channel, _on_remote)

        return q

    def unregister_queue(
        self, tenant_id: str, recipient: str, q: asyncio.Queue
    ) -> None:
        """Remove a queue when the SSE connection closes."""
        key = self._queue_key(tenant_id, recipient)
        queues = self._queues.get(key, [])
        if q in queues:
            queues.remove(q)

        # Unsubscribe from cross-replica notifications
        if self._notification:
            handler = getattr(q, "_remote_handler", None)
            channel = getattr(q, "_remote_channel", None)
            if handler and channel:
                self._notification.unsubscribe(channel, handler)
