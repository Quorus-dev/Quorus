"""In-memory QueueBackend implementation — used for development and testing."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from murmur.storage.backend import QueueBackend


class InMemoryBackend:
    """In-memory queue backend. Messages live in dicts, scoped by tenant_id."""

    def __init__(self) -> None:
        # (tenant_id, to_name) -> list[dict]
        self._queues: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def enqueue(self, tenant_id: str, to_name: str, message: dict) -> None:
        async with self._lock:
            self._queues[(tenant_id, to_name)].append(message)

    async def dequeue_all(self, tenant_id: str, to_name: str) -> list[dict]:
        async with self._lock:
            key = (tenant_id, to_name)
            messages = list(self._queues[key])
            self._queues[key].clear()
            return messages

    async def peek(self, tenant_id: str, to_name: str) -> int:
        async with self._lock:
            return len(self._queues.get((tenant_id, to_name), []))

    async def enqueue_batch(self, tenant_id: str, to_name: str, messages: list[dict]) -> None:
        async with self._lock:
            self._queues[(tenant_id, to_name)].extend(messages)

    def clear(self) -> None:
        """Reset all queues (for testing)."""
        self._queues.clear()


# Verify protocol compliance at import time
assert isinstance(InMemoryBackend(), QueueBackend)
