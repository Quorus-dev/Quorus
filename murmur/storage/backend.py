"""QueueBackend protocol — defines the storage interface for DM delivery queues."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class QueueBackend(Protocol):
    """Protocol for DM delivery queue storage.

    enqueue = INSERT a message, dequeue_all = SELECT + mark delivered.
    Scoped by tenant_id for multi-tenant isolation.
    """

    async def enqueue(self, tenant_id: str, to_name: str, message: dict) -> None:
        """Store a message for the given recipient within a tenant."""
        ...

    async def dequeue_all(self, tenant_id: str, to_name: str) -> list[dict]:
        """Fetch and mark as delivered all pending messages for a recipient."""
        ...

    async def peek(self, tenant_id: str, to_name: str) -> int:
        """Return count of pending messages without consuming them."""
        ...

    async def enqueue_batch(self, tenant_id: str, to_name: str, messages: list[dict]) -> None:
        """Store multiple messages for a recipient (used for chunked messages)."""
        ...
