"""In-memory QueueBackend implementation — used for development and testing."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

from murmur.storage.backend import QueueBackend


class InMemoryBackend:
    """In-memory queue backend. Messages live in dicts, scoped by tenant_id."""

    def __init__(self) -> None:
        # (tenant_id, to_name) -> list[dict]
        self._queues: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._inflight: dict[tuple[str, str, str], list[dict]] = {}
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

    async def enqueue_batch(self, tenant_id: str, to_name: str, messages: list[dict]) -> None:
        async with self._lock:
            self._queues[(tenant_id, to_name)].extend(messages)

    def clear(self) -> None:
        """Reset all queues (for testing)."""
        self._queues.clear()
        self._inflight.clear()


# Verify protocol compliance at import time
assert isinstance(InMemoryBackend(), QueueBackend)
