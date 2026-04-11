"""SSE (Server-Sent Events) service — token management and push delivery.

SSE queues are process-local (connection-scoped), while token storage is
delegated to an SSETokenBackend that may be in-memory or Redis-backed.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import structlog

from murmur.backends.protocol import SSETokenBackend

logger = structlog.get_logger("murmur.services.sse")


class SSEService:
    """Manages SSE authentication tokens and per-recipient push queues."""

    def __init__(
        self,
        backend: SSETokenBackend,
        max_queue_size: int = 1000,
    ) -> None:
        self._backend = backend
        self._max_queue_size = max_queue_size
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
        """Push *message* to all active SSE connections for *recipient*."""
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
        """Create and register a new SSE queue for a connection."""
        key = self._queue_key(tenant_id, recipient)
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._queues[key].append(q)
        return q

    def unregister_queue(
        self, tenant_id: str, recipient: str, q: asyncio.Queue
    ) -> None:
        """Remove a queue when the SSE connection closes."""
        key = self._queue_key(tenant_id, recipient)
        queues = self._queues.get(key, [])
        if q in queues:
            queues.remove(q)
