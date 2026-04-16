# Redis Cost Tightening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut idle Redis command throughput by ≥95% so the live Quorus relay stops exhausting the Upstash 500K/month free tier in 3 days.

**Architecture:** Three orthogonal changes — (1) webhook worker switches from 1s polling to native blocking `XREADGROUP` and throttles reclaim/promote to a 30s wall-clock timer; (2) `RedisPresenceBackend.list_all()` gets a per-tenant TTL cache invalidated on heartbeat write; (3) `NotFoundRateLimitMiddleware` keeps a process-local LRU of `(ip, path)` non-404 hits so repeat requests skip the Redis read.

**Tech Stack:** Python 3.10+ async, FastAPI, redis-py async, pytest, pytest-asyncio, fakeredis (already in dev deps), structlog, ruff.

---

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `quorus/backends/protocol.py` | Add optional `fetch_blocking` to `WebhookQueueBackend`. | Modify |
| `quorus/backends/redis_backends.py` | (a) Implement `RedisWebhookQueueBackend.fetch_blocking` using native `XREADGROUP ... BLOCK`. (b) Throttle `XAUTOCLAIM` + `promote_delayed` to a 30s timer kept on the instance. (c) Cache `ZCARD webhook:delay` for 10s. (d) Wrap `RedisPresenceBackend.list_all` with a TTL cache, invalidated on `heartbeat` write. | Modify |
| `quorus/backends/memory.py` | Add `fetch_blocking` to `InMemoryWebhookQueueBackend` so the protocol stays uniform; uses `asyncio.Event` to wake immediately on enqueue. | Modify |
| `quorus/services/webhook_svc.py` | `_worker_loop_durable` calls `fetch_blocking(count=10, block_ms=30000)` instead of poll-then-sleep. Drop the unconditional `await asyncio.sleep(1.0)` paths. Keep the failure backoff on exception. | Modify |
| `quorus/relay.py` (`NotFoundRateLimitMiddleware`, line 581) | Add a process-local `OrderedDict` LRU keyed `(client_ip, path)` of last-seen-good timestamps. Skip the Redis read on cache hit. Insert on response status < 400. | Modify |
| `tests/test_webhook_durable_blocking.py` | New: blocking fetch returns immediately on enqueue, returns empty after block timeout, reclaim timer fires on schedule, promote skipped when delay set empty. | Create |
| `tests/test_presence_cache.py` | New: `list_all` hits Redis once per cache window, heartbeat invalidates, cache disabled when TTL=0. | Create |
| `tests/test_not_found_lru.py` | New: second request from same IP to known-good path skips `is_rate_limited`. | Create |
| `tests/test_webhook_durable.py` | Existing (if present) — extend to cover the new fetch path; keep old `fetch()` semantics intact. | Verify / extend |

**No file should grow past 500 lines. `redis_backends.py` is already 1,775 lines — that's pre-existing tech debt; we add minimal code (≤60 net new lines) but do not refactor in this plan.**

---

## Task 1: Add `fetch_blocking` to the `WebhookQueueBackend` protocol

**Files:**
- Modify: `quorus/backends/protocol.py:426-475`

- [ ] **Step 1: Read the existing protocol**

Run: `sed -n '426,475p' quorus/backends/protocol.py` and confirm the methods present today are `enqueue`, `fetch`, `ack`, `nack`, `promote_delayed`, `get_dlq`, `get_stats`.

- [ ] **Step 2: Add the new optional method to the Protocol**

Edit `quorus/backends/protocol.py`. After the existing `fetch` method (around line 445) insert:

```python
    async def fetch_blocking(
        self, count: int = 10, block_ms: int = 30000
    ) -> list[tuple[str, dict]]:
        """Fetch jobs, blocking up to ``block_ms`` ms when none are pending.

        Backends should return as soon as at least one job is available.
        Returns an empty list when the block timeout expires with no work.
        Reclaim of stale claims and promotion of delayed jobs MAY happen
        less frequently than every call (see implementation notes).
        """
        ...
```

- [ ] **Step 3: Commit**

```bash
git add quorus/backends/protocol.py
git commit -m "feat(backends): add fetch_blocking to WebhookQueueBackend protocol"
git push
```

---

## Task 2: Implement `fetch_blocking` on the in-memory queue (TDD: test first)

**Files:**
- Test: `tests/test_webhook_durable_blocking.py` (create)
- Modify: `quorus/backends/memory.py` (`InMemoryWebhookQueueBackend`, around line 890-985)

- [ ] **Step 1: Write the failing test**

Create `tests/test_webhook_durable_blocking.py`:

```python
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
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `pytest tests/test_webhook_durable_blocking.py -v`
Expected: `AttributeError: 'InMemoryWebhookQueueBackend' object has no attribute 'fetch_blocking'` (both tests fail).

- [ ] **Step 3: Add the `fetch_blocking` method to `InMemoryWebhookQueueBackend`**

Edit `quorus/backends/memory.py`. In `__init__` (around line 893), add an `asyncio.Event` for waking blocked fetchers:

```python
    def __init__(self, dlq_max_size: int = 1000) -> None:
        self._queue: list[tuple[str, dict]] = []  # (job_id, job_data)
        self._delayed: list[tuple[float, dict]] = []  # (retry_at, job_data)
        self._pending: dict[str, dict] = {}  # job_id -> job_data
        self._dlq: list[dict] = []
        self._dlq_max_size = dlq_max_size
        self._counter = 0
        self._lock = asyncio.Lock()
        self._has_work = asyncio.Event()  # set when queue is non-empty
```

In `enqueue` (around line 902), set the event after adding work. Replace the existing method:

```python
    async def enqueue(self, job: dict) -> str:
        """Add a webhook job to the queue. Returns job ID."""
        async with self._lock:
            self._counter += 1
            job_id = f"job-{self._counter}"
            self._queue.append((job_id, job))
            self._has_work.set()
            return job_id
```

In `fetch` (around line 910), clear the event when the queue drains. Replace:

```python
    async def fetch(self, count: int = 10) -> list[tuple[str, dict]]:
        """Fetch pending jobs for delivery."""
        async with self._lock:
            self._promote_delayed_locked()
            fetched = self._queue[:count]
            self._queue = self._queue[count:]
            for job_id, job in fetched:
                self._pending[job_id] = job
            if not self._queue:
                self._has_work.clear()
            return fetched
```

Add `fetch_blocking` immediately after `fetch`:

```python
    async def fetch_blocking(
        self, count: int = 10, block_ms: int = 30000
    ) -> list[tuple[str, dict]]:
        """Block up to ``block_ms`` ms waiting for at least one job."""
        # Fast path — work already available.
        jobs = await self.fetch(count=count)
        if jobs:
            return jobs
        # Slow path — wait for the event or the timeout.
        try:
            await asyncio.wait_for(self._has_work.wait(), timeout=block_ms / 1000)
        except asyncio.TimeoutError:
            return []
        return await self.fetch(count=count)
```

- [ ] **Step 4: Re-run the test**

Run: `pytest tests/test_webhook_durable_blocking.py::test_memory_fetch_blocking_returns_immediately_on_enqueue tests/test_webhook_durable_blocking.py::test_memory_fetch_blocking_returns_empty_on_timeout -v`
Expected: both PASS.

- [ ] **Step 5: Commit and push**

```bash
git add tests/test_webhook_durable_blocking.py quorus/backends/memory.py
git commit -m "feat(backends): in-memory webhook queue fetch_blocking"
git push
```

---

## Task 3: Implement `fetch_blocking` on the Redis queue (TDD)

**Files:**
- Modify: `tests/test_webhook_durable_blocking.py` (extend)
- Modify: `quorus/backends/redis_backends.py` (`RedisWebhookQueueBackend`, around lines 1209-1342)

- [ ] **Step 1: Write the failing Redis test**

Append to `tests/test_webhook_durable_blocking.py`:

```python
import fakeredis.aioredis  # noqa: E402

from quorus.backends.redis_backends import RedisWebhookQueueBackend  # noqa: E402


@pytest.fixture
async def fake_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
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
    assert 0.15 <= elapsed < 1.5, f"expected ~0.2s wait, got {elapsed}s"
```

- [ ] **Step 2: Confirm the new tests fail**

Run: `pytest tests/test_webhook_durable_blocking.py -v -k redis`
Expected: `AttributeError: 'RedisWebhookQueueBackend' object has no attribute 'fetch_blocking'`.

- [ ] **Step 3: Implement `fetch_blocking` and the throttle on the Redis backend**

Edit `quorus/backends/redis_backends.py`. In the `RedisWebhookQueueBackend` class (line 1209), find `__init__` (line 1219). Replace it with:

```python
    def __init__(self, r: Redis) -> None:
        self._r = r
        self._group_created = False
        # Throttle for housekeeping calls (xautoclaim + promote_delayed).
        # Without this, every fetch issues 2-3 extra commands.
        self._reclaim_interval_s = float(
            os.getenv("QUORUS_WEBHOOK_RECLAIM_INTERVAL", "30")
        )
        self._last_reclaim_at: float = 0.0
        # Cache for ZCARD on the delay set so we can skip the EVAL
        # promote_delayed when there is nothing to promote.
        self._delay_card_cache: tuple[float, int] = (0.0, 0)
        self._delay_card_ttl_s = float(
            os.getenv("QUORUS_WEBHOOK_DELAY_CARD_TTL", "10")
        )
```

Note: `os` is already imported at the top of the file — confirm with `grep -n "^import os" quorus/backends/redis_backends.py`. If not present, add `import os` at the top.

Below `enqueue` (around line 1240), add a private helper:

```python
    async def _delay_set_has_work(self) -> bool:
        """Cache ZCARD on the delay set to skip promote_delayed when empty."""
        now = time.time()
        cached_at, cached_count = self._delay_card_cache
        if now - cached_at < self._delay_card_ttl_s:
            return cached_count > 0
        try:
            count = await _with_timeout(self._r.zcard(_WEBHOOK_DELAY_KEY))
        except Exception:
            # On error, assume there is work so we don't drop retries silently.
            return True
        self._delay_card_cache = (now, int(count or 0))
        return self._delay_card_cache[1] > 0
```

Replace the existing `fetch` (around line 1242) so the housekeeping is gated on the timer + cache. The new body:

```python
    async def fetch(self, count: int = 10) -> list[tuple[str, dict]]:
        """Fetch pending jobs for delivery.

        Housekeeping (XAUTOCLAIM, promote_delayed) runs at most once
        per ``self._reclaim_interval_s`` seconds to keep idle Redis
        command rate near zero.
        """
        await self._ensure_group()
        jobs: list[tuple[str, dict]] = []

        now = time.time()
        do_housekeeping = (now - self._last_reclaim_at) >= self._reclaim_interval_s
        if do_housekeeping:
            self._last_reclaim_at = now
            # 0. Promote delayed → main, but only if the delay set has work.
            if await self._delay_set_has_work():
                await self.promote_delayed()
            # 1. Reclaim stale pending jobs.
            visibility_ms = _WEBHOOK_VISIBILITY_TIMEOUT * 1000
            try:
                claimed = await _with_timeout(self._r.xautoclaim(
                    _WEBHOOK_QUEUE_KEY, _WEBHOOK_CG, _WEBHOOK_CONSUMER,
                    min_idle_time=visibility_ms, start_id="0-0", count=count
                ))
                if claimed and len(claimed) >= 2:
                    for entry_id, fields in claimed[1]:
                        job = json.loads(fields["data"])
                        jobs.append((entry_id, job))
            except ResponseError as e:
                _redis_logger.warning("XAUTOCLAIM failed: %s", e)

        # 2. Read new jobs (always — this is the cheap one).
        remaining = count - len(jobs)
        if remaining > 0:
            entries = await _with_timeout(self._r.xreadgroup(
                _WEBHOOK_CG, _WEBHOOK_CONSUMER,
                {_WEBHOOK_QUEUE_KEY: ">"}, count=remaining
            ))
            if entries:
                for _stream_key, stream_entries in entries:
                    for entry_id, fields in stream_entries:
                        job = json.loads(fields["data"])
                        jobs.append((entry_id, job))

        return jobs
```

Add `fetch_blocking` immediately after `fetch`:

```python
    async def fetch_blocking(
        self, count: int = 10, block_ms: int = 30000
    ) -> list[tuple[str, dict]]:
        """Block on XREADGROUP up to ``block_ms`` ms.

        Always runs the cheap read first (so cached pending entries are
        delivered without waiting). On empty, issues a single
        ``XREADGROUP ... BLOCK block_ms``. Housekeeping (reclaim,
        promote_delayed) is still throttled by the timer in ``fetch``.
        """
        jobs = await self.fetch(count=count)
        if jobs:
            return jobs
        await self._ensure_group()
        try:
            entries = await _with_timeout(
                self._r.xreadgroup(
                    _WEBHOOK_CG, _WEBHOOK_CONSUMER,
                    {_WEBHOOK_QUEUE_KEY: ">"}, count=count, block=block_ms,
                ),
                # Allow the BLOCK to elapse — give the wait_for a margin
                # over block_ms so we don't race the Redis timer.
                timeout_override=(block_ms / 1000) + 5.0,
            )
        except RedisOperationTimeout:
            return []
        if not entries:
            return []
        out: list[tuple[str, dict]] = []
        for _stream_key, stream_entries in entries:
            for entry_id, fields in stream_entries:
                job = json.loads(fields["data"])
                out.append((entry_id, job))
        return out
```

- [ ] **Step 4: Add `timeout_override` support to `_with_timeout`**

Find `_with_timeout` near the top of `quorus/backends/redis_backends.py` (search: `grep -n "def _with_timeout" quorus/backends/redis_backends.py`). Replace its definition with:

```python
async def _with_timeout(coro, *, timeout_override: float | None = None):
    """Wrap a Redis call with a timeout.

    Most calls use REDIS_OP_TIMEOUT_SECONDS. Pass ``timeout_override`` for
    long-blocking commands (e.g. XREADGROUP ... BLOCK) where the caller
    knows the expected wait exceeds the default.
    """
    timeout = timeout_override if timeout_override is not None else REDIS_OP_TIMEOUT_SECONDS
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as e:
        raise RedisOperationTimeout(str(e)) from e
```

If the existing signature already accepts `timeout_override` (defensive against partial future merges), leave the file alone and verify with `pytest`.

- [ ] **Step 5: Run all blocking tests**

Run: `pytest tests/test_webhook_durable_blocking.py -v`
Expected: all four tests PASS.

- [ ] **Step 6: Run the full webhook test suite to catch regressions**

Run: `pytest tests/ -v -k webhook`
Expected: all PASS (or pre-existing failures only — note any flake count for the final review).

- [ ] **Step 7: Commit and push**

```bash
git add quorus/backends/redis_backends.py tests/test_webhook_durable_blocking.py
git commit -m "feat(backends): redis webhook queue blocking fetch + reclaim throttle"
git push
```

---

## Task 4: Switch `WebhookService._worker_loop_durable` to blocking fetch

**Files:**
- Modify: `quorus/services/webhook_svc.py:405-420`

- [ ] **Step 1: Confirm the protocol exposes `fetch_blocking`**

Run: `grep -n "fetch_blocking" quorus/backends/protocol.py quorus/backends/memory.py quorus/backends/redis_backends.py`
Expected: matches in all three files.

- [ ] **Step 2: Replace the worker loop body**

Edit `quorus/services/webhook_svc.py`. Replace `_worker_loop_durable` (lines 405-420):

```python
    async def _worker_loop_durable(self) -> None:
        """Worker loop for durable Redis Streams queue.

        Uses native blocking XREADGROUP (or asyncio.Event on the in-memory
        backend) so we wake on enqueue rather than polling on a timer.
        Idle cost: ~1 Redis command per ``WEBHOOK_BLOCK_MS`` ms instead
        of 3-4 per second.
        """
        block_ms = int(os.environ.get("WEBHOOK_BLOCK_MS", "30000"))
        while not self._stop_event.is_set():
            try:
                jobs = await self._queue_backend.fetch_blocking(
                    count=10, block_ms=block_ms
                )
            except Exception as e:
                logger.warning("Failed to fetch webhook jobs: %s", e)
                await asyncio.sleep(1.0)
                continue

            if not jobs:
                # Block timeout elapsed with no work — loop back and block again.
                continue

            for job_id, job_data in jobs:
                await self._process_job_durable(job_id, job_data)
```

- [ ] **Step 3: Run the webhook service tests**

Run: `pytest tests/ -v -k webhook`
Expected: all PASS. Pay attention to any test that mocked `fetch` — those should still pass because we did not remove `fetch`, but if a test relied on the old 1-second poll cadence it may need updating. If a test now hangs, give it `block_ms=100` via the new env var: `WEBHOOK_BLOCK_MS=100 pytest ...`.

- [ ] **Step 4: Commit and push**

```bash
git add quorus/services/webhook_svc.py
git commit -m "perf(webhook): use blocking fetch instead of 1s poll loop"
git push
```

---

## Task 5: TTL cache for `RedisPresenceBackend.list_all` (TDD)

**Files:**
- Test: `tests/test_presence_cache.py` (create)
- Modify: `quorus/backends/redis_backends.py:734-795`

- [ ] **Step 1: Write the failing test**

Create `tests/test_presence_cache.py`:

```python
"""Verify presence list_all is cached per tenant and invalidated on heartbeat."""

from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from quorus.backends.redis_backends import RedisPresenceBackend


@pytest.fixture
async def fake_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_list_all_caches_within_ttl(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("QUORUS_PRESENCE_CACHE_TTL", "5")
    backend = RedisPresenceBackend(fake_redis)
    await backend.heartbeat("tA", "alice", "online", "room1")
    await backend.heartbeat("tA", "bob", "online", "room1")

    # First call hits Redis.
    first = await backend.list_all("tA", timeout_seconds=60)
    assert len(first) == 2

    # Mutate Redis directly behind the cache's back to prove the second
    # call did NOT re-read.
    await fake_redis.zadd("t:tA:presence:_idx", {"carol": 1.0})
    await fake_redis.hset(
        "t:tA:presence:carol",
        mapping={"name": "carol", "status": "online", "room": "room1",
                 "last_heartbeat": "2026-01-01T00:00:00+00:00",
                 "uptime_start": "2026-01-01T00:00:00+00:00",
                 "tenant_id": "tA"},
    )
    cached = await backend.list_all("tA", timeout_seconds=60)
    assert len(cached) == 2, "second call within TTL should be served from cache"


@pytest.mark.asyncio
async def test_heartbeat_invalidates_cache(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("QUORUS_PRESENCE_CACHE_TTL", "5")
    backend = RedisPresenceBackend(fake_redis)
    await backend.heartbeat("tA", "alice", "online", "room1")

    first = await backend.list_all("tA", timeout_seconds=60)
    assert len(first) == 1

    # New heartbeat invalidates the cache so the next read is fresh.
    await backend.heartbeat("tA", "bob", "online", "room1")
    fresh = await backend.list_all("tA", timeout_seconds=60)
    assert len(fresh) == 2


@pytest.mark.asyncio
async def test_cache_disabled_when_ttl_zero(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("QUORUS_PRESENCE_CACHE_TTL", "0")
    backend = RedisPresenceBackend(fake_redis)
    await backend.heartbeat("tA", "alice", "online", "room1")

    first = await backend.list_all("tA", timeout_seconds=60)
    # Mutate behind the back; with TTL=0 the next call must re-read.
    await fake_redis.zadd("t:tA:presence:_idx", {"bob": 1.0})
    await fake_redis.hset(
        "t:tA:presence:bob",
        mapping={"name": "bob", "status": "online", "room": "room1",
                 "last_heartbeat": "2026-01-01T00:00:00+00:00",
                 "uptime_start": "2026-01-01T00:00:00+00:00",
                 "tenant_id": "tA"},
    )
    second = await backend.list_all("tA", timeout_seconds=60)
    assert len(second) == 2, "ttl=0 must disable cache"


@pytest.mark.asyncio
async def test_cache_keyed_by_tenant(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("QUORUS_PRESENCE_CACHE_TTL", "60")
    backend = RedisPresenceBackend(fake_redis)
    await backend.heartbeat("tA", "alice", "online", "room1")
    await backend.heartbeat("tB", "bob", "online", "room1")

    # Prime tenant A's cache.
    a_first = await backend.list_all("tA", timeout_seconds=60)
    assert len(a_first) == 1

    # Tenant B's first call must still hit Redis (different cache key).
    b_first = await backend.list_all("tB", timeout_seconds=60)
    assert len(b_first) == 1
    assert b_first[0]["name"] == "bob"
```

- [ ] **Step 2: Run the test and confirm failures**

Run: `pytest tests/test_presence_cache.py -v`
Expected: `test_list_all_caches_within_ttl` and `test_cache_disabled_when_ttl_zero` fail (cache not present yet); the other two may pass for unrelated reasons. All four should pass after implementation.

- [ ] **Step 3: Add the cache to `RedisPresenceBackend`**

Edit `quorus/backends/redis_backends.py`. Replace `RedisPresenceBackend.__init__` (around line 737-738):

```python
    def __init__(self, r: Redis) -> None:
        self._r = r
        # Per-tenant TTL cache for list_all to absorb dashboard / TUI polls.
        # Key: (tenant_id, timeout_seconds). Value: (results_list, fetched_at).
        # TTL is read at call time so tests can monkeypatch the env var.
        self._list_all_cache: dict[tuple[str, int], tuple[list[dict], float]] = {}
```

Replace `heartbeat` (around line 746-762) so writes invalidate the cache:

```python
    async def heartbeat(self, tenant_id: str, name: str, status: str, room: str) -> dict:
        now = datetime.now(timezone.utc)
        now_iso, now_ts = now.isoformat(), now.timestamp()
        entry_key = self._entry_key(tenant_id, name)
        existing_start = await _with_timeout(self._r.hget(entry_key, "uptime_start"))
        entry = {
            "name": name, "status": status, "room": room,
            "last_heartbeat": now_iso,
            "uptime_start": existing_start or now_iso,
            "tenant_id": tenant_id,
        }
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hset(entry_key, mapping=entry)
            pipe.zadd(self._index_key(tenant_id), {name: now_ts})
            await _with_timeout(pipe.execute())
        # Invalidate any cached list_all for this tenant.
        for key in [k for k in self._list_all_cache if k[0] == tenant_id]:
            self._list_all_cache.pop(key, None)
        return entry
```

Replace `list_all` (around line 764-795):

```python
    async def list_all(self, tenant_id: str, timeout_seconds: int) -> list[dict]:
        """Return ALL presence entries for the tenant.

        Cached per ``(tenant_id, timeout_seconds)`` for
        ``QUORUS_PRESENCE_CACHE_TTL`` seconds (default 5). Set the env
        var to 0 to disable the cache.
        """
        ttl = float(os.getenv("QUORUS_PRESENCE_CACHE_TTL", "5"))
        cache_key = (tenant_id, int(timeout_seconds))
        now = time.time()
        if ttl > 0:
            cached = self._list_all_cache.get(cache_key)
            if cached is not None and (now - cached[1]) < ttl:
                # Recompute _online flag from cached scores using the current
                # cutoff so the value doesn't go stale when cached entries
                # are returned across the online/offline boundary.
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
                ).timestamp()
                refreshed: list[dict] = []
                for entry in cached[0]:
                    score = entry.get("_score", 0.0)
                    item = {k: v for k, v in entry.items() if k != "_score"}
                    item["_online"] = score >= cutoff
                    refreshed.append(item)
                return refreshed

        names_with_scores = await _with_timeout(
            self._r.zrange(self._index_key(tenant_id), 0, -1, withscores=True)
        )
        if not names_with_scores:
            if ttl > 0:
                self._list_all_cache[cache_key] = ([], now)
            return []

        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        ).timestamp()
        scores: dict[str, float] = {}
        names: list[str] = []
        for name, score in names_with_scores:
            names.append(name)
            scores[name] = score

        async with self._r.pipeline(transaction=False) as pipe:
            for name in names:
                pipe.hgetall(self._entry_key(tenant_id, name))
            all_data = await _with_timeout(pipe.execute())

        results: list[dict] = []
        cache_payload: list[dict] = []
        for name, data in zip(names, all_data):
            if data:
                score = scores.get(name, 0.0)
                data["_online"] = score >= cutoff
                results.append(data)
                # Store the score on the cached copy so a later read can
                # recompute _online without hitting Redis.
                cache_payload.append({**data, "_score": score})

        if ttl > 0:
            self._list_all_cache[cache_key] = (cache_payload, now)
        return results
```

Verify `time` and `os` are already imported at the top of the file. Run `grep -n "^import time\|^import os" quorus/backends/redis_backends.py` — both should match.

- [ ] **Step 4: Run the cache tests**

Run: `pytest tests/test_presence_cache.py -v`
Expected: all four PASS.

- [ ] **Step 5: Run the broader presence test suite**

Run: `pytest tests/ -v -k presence`
Expected: all PASS. If any test relies on `list_all` being uncached, it will need to set `QUORUS_PRESENCE_CACHE_TTL=0` via `monkeypatch.setenv`. Update those tests.

- [ ] **Step 6: Commit and push**

```bash
git add quorus/backends/redis_backends.py tests/test_presence_cache.py
git commit -m "perf(presence): cache list_all per tenant for 5s"
git push
```

---

## Task 6: LRU short-circuit in `NotFoundRateLimitMiddleware` (TDD)

**Files:**
- Test: `tests/test_not_found_lru.py` (create)
- Modify: `quorus/relay.py:577-645`

- [ ] **Step 1: Write the failing test**

Create `tests/test_not_found_lru.py`:

```python
"""Verify NotFoundRateLimitMiddleware skips Redis for cached good paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quorus.relay import NotFoundRateLimitMiddleware


def _build_app(rate_limit_svc) -> FastAPI:
    app = FastAPI()
    app.state.rate_limit_service = rate_limit_svc

    @app.get("/known")
    async def known() -> dict:
        return {"ok": True}

    app.add_middleware(NotFoundRateLimitMiddleware)
    return app


def test_repeat_known_path_skips_rate_limit_read(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://stub")
    # Reload the module-level REDIS_URL referenced by the middleware.
    import importlib
    import quorus.relay as relay
    importlib.reload(relay)

    svc = MagicMock()
    svc.is_rate_limited = AsyncMock(return_value=False)
    svc.record = AsyncMock()
    app = FastAPI()
    app.state.rate_limit_service = svc

    @app.get("/known")
    async def known() -> dict:
        return {"ok": True}

    app.add_middleware(relay.NotFoundRateLimitMiddleware)

    with TestClient(app) as client:
        # First call → cache miss → one Redis read.
        r1 = client.get("/known")
        assert r1.status_code == 200
        assert svc.is_rate_limited.await_count == 1

        # Second call from the same client → cache hit → no Redis read.
        r2 = client.get("/known")
        assert r2.status_code == 200
        assert svc.is_rate_limited.await_count == 1, "second call must skip Redis"


def test_404_still_records(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://stub")
    import importlib
    import quorus.relay as relay
    importlib.reload(relay)

    svc = MagicMock()
    svc.is_rate_limited = AsyncMock(return_value=False)
    svc.record = AsyncMock()
    app = FastAPI()
    app.state.rate_limit_service = svc
    app.add_middleware(relay.NotFoundRateLimitMiddleware)

    with TestClient(app) as client:
        r = client.get("/does-not-exist")
        assert r.status_code == 404
        assert svc.record.await_count == 1
```

- [ ] **Step 2: Confirm the tests fail**

Run: `pytest tests/test_not_found_lru.py -v`
Expected: `test_repeat_known_path_skips_rate_limit_read` FAILS with `assert 2 == 1` (today every request hits the limiter).

- [ ] **Step 3: Add the LRU to the middleware**

Edit `quorus/relay.py`. Above the `_NOT_FOUND_EXEMPT_PATHS` definition (line 577), add:

```python
from collections import OrderedDict


# Process-local LRU of (client_ip, path) tuples that have returned
# < 400 in the last NOT_FOUND_LRU_TTL seconds. We use this to skip
# the Redis is_rate_limited read on repeat traffic from the same
# client to known-good endpoints — the dominant idle pattern.
_NOT_FOUND_LRU_MAX = int(os.environ.get("NOT_FOUND_LRU_MAX", "4096"))
_NOT_FOUND_LRU_TTL = int(os.environ.get("NOT_FOUND_LRU_TTL", "60"))
_not_found_good_lru: "OrderedDict[tuple[str, str], float]" = OrderedDict()
_not_found_lru_lock = asyncio.Lock()


async def _lru_is_known_good(client_ip: str, path: str) -> bool:
    """Return True if (ip, path) has been seen returning < 400 recently."""
    key = (client_ip, path)
    now = time.time()
    async with _not_found_lru_lock:
        ts = _not_found_good_lru.get(key)
        if ts is None:
            return False
        if now - ts > _NOT_FOUND_LRU_TTL:
            _not_found_good_lru.pop(key, None)
            return False
        # Refresh recency.
        _not_found_good_lru.move_to_end(key)
        return True


async def _lru_mark_known_good(client_ip: str, path: str) -> None:
    """Insert (ip, path) into the LRU; evict oldest when over capacity."""
    key = (client_ip, path)
    now = time.time()
    async with _not_found_lru_lock:
        _not_found_good_lru[key] = now
        _not_found_good_lru.move_to_end(key)
        while len(_not_found_good_lru) > _NOT_FOUND_LRU_MAX:
            _not_found_good_lru.popitem(last=False)
```

Then replace `NotFoundRateLimitMiddleware.dispatch` (lines 588-645) — note the path-exempt early-return stays; we add LRU short-circuit just below it:

```python
    async def dispatch(self, request: Request, call_next):
        from starlette.responses import JSONResponse

        # Exempt infrastructure paths from 404 rate limiting.
        if request.url.path in _NOT_FOUND_EXEMPT_PATHS:
            return await call_next(request)

        client_ip = _get_client_ip(request)
        path = request.url.path

        # LRU short-circuit: if this (ip, path) has been good recently,
        # skip the Redis read entirely. Still go through call_next so
        # 404 recording at the bottom remains correct on miss.
        skip_pre_check = await _lru_is_known_good(client_ip, path)

        rate_limit_svc = getattr(request.app.state, "rate_limit_service", None)
        use_redis = rate_limit_svc is not None and REDIS_URL

        if not skip_pre_check:
            if use_redis:
                blocked = await rate_limit_svc.is_rate_limited(
                    "global", f"404:{client_ip}", NOT_FOUND_LIMIT, window=NOT_FOUND_WINDOW
                )
                if blocked:
                    logger.warning(
                        "Blocking IP for repeated 404s (Redis)",
                        ip=client_ip, limit=NOT_FOUND_LIMIT, window=NOT_FOUND_WINDOW,
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": "Too many requests to non-existent resources. "
                            "Please verify your room/resource IDs."
                        },
                        headers={"Retry-After": str(NOT_FOUND_BLOCK_DURATION)},
                    )
            else:
                blocked, retry_after = await _is_blocked_memory(client_ip)
                if blocked:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": "Too many requests to non-existent resources. "
                            "Please verify your room/resource IDs."
                        },
                        headers={"Retry-After": str(retry_after)},
                    )

        response = await call_next(request)

        if response.status_code == 404:
            if use_redis:
                await rate_limit_svc.record(
                    "global", f"404:{client_ip}", window=NOT_FOUND_WINDOW
                )
            else:
                await _record_not_found_memory(client_ip)
        elif response.status_code < 400:
            # Mark this path good so future requests from the same IP skip
            # the limiter check.
            await _lru_mark_known_good(client_ip, path)

        return response
```

- [ ] **Step 4: Re-run the LRU tests**

Run: `pytest tests/test_not_found_lru.py -v`
Expected: both PASS.

- [ ] **Step 5: Run the full relay test suite**

Run: `pytest tests/ -v -k "relay or middleware or not_found"`
Expected: all PASS.

- [ ] **Step 6: Commit and push**

```bash
git add quorus/relay.py tests/test_not_found_lru.py
git commit -m "perf(relay): LRU short-circuit for 404 rate limiter on good paths"
git push
```

---

## Task 7: Full-suite verification + lint

**Files:** none modified.

- [ ] **Step 1: Lint**

Run: `ruff check .`
Expected: no violations. If any, fix and re-run.

- [ ] **Step 2: Full test suite**

Run: `pytest -v`
Expected: pass count ≥ 905 (the baseline noted in CONTEXT.md). New tests: 9 (Task 2: 2, Task 3: 2, Task 5: 4, Task 6: 2 + 1 path-exempt regression). Note the new total in CONTEXT.md when committing the doc updates.

- [ ] **Step 3: Burn-rate sanity (manual, optional)**

Spin a real Redis (or fakeredis) and the relay locally with `WEBHOOK_BLOCK_MS=2000` for fast iteration:

```bash
DATABASE_URL=postgres://... REDIS_URL=redis://localhost:6379 \
QUORUS_PRESENCE_CACHE_TTL=5 WEBHOOK_BLOCK_MS=30000 \
uvicorn quorus.relay:app --port 8080 &
RELAY_PID=$!
sleep 5
# Count Redis commands over a 30s idle window using redis-cli MONITOR:
( redis-cli MONITOR & MON=$!; sleep 30; kill $MON ) | wc -l
kill $RELAY_PID
```

Expected: well under 50 commands in 30s (target was <5/min for the webhook worker, plus ~1 PING per 30s for the cached health check, plus Postgres-only pings on /health).

- [ ] **Step 4: Update CONTEXT.md**

Edit `CONTEXT.md`:
- Bump test count to the new total.
- Add to "Recent Changes" at the top of the table:

```
| 2026-04-16 | perf: cut idle Redis traffic ~95% (block fetch, presence cache, LRU)     |
```

- Drop the oldest entry to keep the list at 10.

- [ ] **Step 5: Commit and push**

```bash
git add CONTEXT.md
git commit -m "docs: note redis cost tightening in CONTEXT"
git push
```

---

## Task 8 (manual, out of code): recreate Upstash db + redeploy

This task is operational, not code. It unbricks the live relay before the May 1 monthly reset.

- [ ] **Step 1: Provision a fresh Upstash Redis db**

In the Upstash console: New Database → free tier → region `ca-central-1` (matches Fly app region) → name `quorus-relay-redis-2`. Copy the `redis://` URL with the password embedded.

- [ ] **Step 2: Roll the Fly secret**

Run: `fly secrets set REDIS_URL='<new-url>' -a quorus-relay`
Expected: Fly redeploys (`Update available... deploying...`); takes ~2 min.

- [ ] **Step 3: Verify the relay is healthy**

Run: `curl -sS https://quorus-relay.fly.dev/health` and confirm `{"status":"ok","postgres":"connected","redis":"connected"}`.

- [ ] **Step 4: Watch Upstash for ~1 hour**

Open the new db's command graph. Expect <50 commands in the first 5 minutes of idle. If the rate is still ≥1 cmd/sec, something didn't take effect — check `fly logs -a quorus-relay` for `WEBHOOK_BLOCK_MS` and `QUORUS_PRESENCE_CACHE_TTL` env or worker startup messages.

- [ ] **Step 5: Schedule deletion of the old db**

In Upstash, mark the old db for deletion in 24h. Keeps a recovery window in case the new db has issues.

---

## Self-Review

I scanned this plan against the spec.

**1. Spec coverage:** Spec called for three changes. Task 1-4 covers blocking-fetch + reclaim throttle (#1 webhook worker). Task 5 covers the presence cache (#2 cache list_all). Task 6 covers the LRU on `NotFoundRateLimitMiddleware` (#3). Task 7 is verification, Task 8 is the operational redeploy. Coverage: complete.

**2. Placeholder scan:** No "TBD", "TODO", or "implement later" in any step. Every code-changing step has the literal new code shown. Test code is concrete with assertions. Commands are runnable.

**3. Type consistency:** `fetch_blocking(count, block_ms)` signature is identical across protocol, in-memory, Redis, and the worker call site. `_with_timeout(coro, *, timeout_override=None)` is the single-place change to the helper. Cache key for presence is `(tenant_id, int(timeout_seconds))` everywhere.

**4. One ambiguity I caught and fixed inline:** The first draft had `fetch_blocking` skipping the housekeeping path for blocking reads. That would have been a correctness bug — stale claims would never be reclaimed. Final design routes both `fetch` and `fetch_blocking` through the same throttled housekeeping (timer-gated), so the behavior is preserved.

**5. Risk I noted but did not eliminate:** The blocking `XREADGROUP` holds a Redis connection for up to `WEBHOOK_BLOCK_MS` (30s). One worker per relay process, Upstash free tier allows 30 connections. Healthy. If the relay scales to multiple Fly machines we may want to revisit, but that's not today's problem.

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-16-redis-cost-tightening.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

You said "go ahead, break into smaller commits, keep pushing them" — that maps directly to **Inline Execution**, which is what I'll do. Calling executing-plans now unless you stop me.
