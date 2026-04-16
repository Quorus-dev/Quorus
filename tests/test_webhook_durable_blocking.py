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


# -- Redis backend ---------------------------------------------------------

import fakeredis.aioredis  # noqa: E402

from quorus.backends.redis_backends import RedisWebhookQueueBackend  # noqa: E402


@pytest.fixture
async def fake_redis():
    """A fakeredis client with an xreadgroup shim that honors ``block=``.

    fakeredis does not natively implement blocking reads — xreadgroup
    returns immediately even with block>0. We wrap the method so tests
    of our blocking path see realistic timing. The shim polls the
    underlying non-blocking xreadgroup every 20ms until data arrives
    or the block timeout elapses.
    """
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    original_xreadgroup = r.xreadgroup

    async def blocking_xreadgroup(*args, **kwargs):
        block = kwargs.pop("block", None)
        if block is None or block <= 0:
            return await original_xreadgroup(*args, **kwargs)
        deadline = time.monotonic() + (block / 1000)
        while True:
            entries = await original_xreadgroup(*args, **kwargs)
            if entries:
                return entries
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.02)

    r.xreadgroup = blocking_xreadgroup
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_redis_fetch_blocking_returns_immediately_on_enqueue(fake_redis) -> None:
    q = RedisWebhookQueueBackend(fake_redis)

    async def producer() -> None:
        await asyncio.sleep(0.05)
        await q.enqueue({"target": "x", "callback_url": "http://x", "payload": {}})

    start = time.monotonic()
    fetcher = asyncio.create_task(q.fetch_blocking(count=10, block_ms=5000))
    asyncio.create_task(producer())
    jobs = await fetcher
    elapsed = time.monotonic() - start
    assert len(jobs) == 1
    assert elapsed < 1.5, f"expected fast wake, took {elapsed}s"


@pytest.mark.asyncio
async def test_redis_fetch_blocking_returns_empty_on_timeout(fake_redis) -> None:
    q = RedisWebhookQueueBackend(fake_redis)
    start = time.monotonic()
    jobs = await q.fetch_blocking(count=10, block_ms=200)
    elapsed = time.monotonic() - start
    assert jobs == []
    assert 0.15 <= elapsed < 2.0, f"expected ~0.2s wait, got {elapsed}s"


@pytest.mark.asyncio
async def test_redis_fetch_throttles_housekeeping(fake_redis, monkeypatch) -> None:
    """Housekeeping (xautoclaim + promote_delayed) must only run once per window."""
    # 1-second throttle for fast test. Set BEFORE constructing the backend —
    # __init__ reads the env var once.
    monkeypatch.setenv("QUORUS_WEBHOOK_RECLAIM_INTERVAL", "1")
    q = RedisWebhookQueueBackend(fake_redis)

    # Stub eval → return 0 (fakeredis has no Lua engine). We just want to
    # count whether xautoclaim was issued.
    async def stub_eval(*args, **kwargs):
        return 0

    monkeypatch.setattr(fake_redis, "eval", stub_eval)

    calls = {"n": 0}
    original = fake_redis.xautoclaim

    async def counting_xautoclaim(*args, **kwargs):
        calls["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(fake_redis, "xautoclaim", counting_xautoclaim)

    # Four rapid fetches within the window → only one housekeeping run.
    await q.fetch(count=10)
    await q.fetch(count=10)
    await q.fetch(count=10)
    await q.fetch(count=10)
    assert calls["n"] == 1, f"expected housekeeping once per window, got {calls['n']}"

    # Advance past the throttle window, then fetch again → one more call.
    await asyncio.sleep(1.1)
    await q.fetch(count=10)
    assert calls["n"] == 2, f"expected 2 housekeeping runs, got {calls['n']}"


@pytest.mark.asyncio
async def test_redis_skips_promote_when_delay_set_empty(fake_redis, monkeypatch) -> None:
    """EVAL promote_delayed must not fire when the delay set has no work."""
    # Always housekeep — isolate the promote_delayed skip logic.
    monkeypatch.setenv("QUORUS_WEBHOOK_RECLAIM_INTERVAL", "0")
    monkeypatch.setenv("QUORUS_WEBHOOK_DELAY_CARD_TTL", "60")  # keep cache long
    q = RedisWebhookQueueBackend(fake_redis)

    eval_calls = {"n": 0}

    async def counting_eval(*args, **kwargs):
        eval_calls["n"] += 1
        return 0

    monkeypatch.setattr(fake_redis, "eval", counting_eval)

    # Delay set is empty → ZCARD returns 0 → EVAL must be skipped.
    await q.fetch(count=10)
    await q.fetch(count=10)
    assert eval_calls["n"] == 0, (
        f"expected no EVAL when delay set is empty, got {eval_calls['n']}"
    )
