"""Cover blocking fetch behaviour for the durable webhook queue backends."""

from __future__ import annotations

import asyncio
import time

import pytest

from quorus.backends.memory import InMemoryWebhookQueueBackend


@pytest.mark.asyncio
async def test_memory_fetch_blocking_returns_immediately_on_enqueue() -> None:
    q = InMemoryWebhookQueueBackend()

    async def producer() -> None:
        await asyncio.sleep(0.05)
        await q.enqueue({"target": "x", "callback_url": "http://x", "payload": {}})

    start = time.monotonic()
    fetcher = asyncio.create_task(q.fetch_blocking(count=10, block_ms=5000))
    asyncio.create_task(producer())
    jobs = await fetcher
    elapsed = time.monotonic() - start

    assert len(jobs) == 1
    assert elapsed < 1.0, f"expected fast wake, took {elapsed}s"


@pytest.mark.asyncio
async def test_memory_fetch_blocking_returns_empty_on_timeout() -> None:
    q = InMemoryWebhookQueueBackend()
    start = time.monotonic()
    jobs = await q.fetch_blocking(count=10, block_ms=200)
    elapsed = time.monotonic() - start
    assert jobs == []
    assert 0.15 <= elapsed < 1.0, f"expected ~0.2s wait, got {elapsed}s"
