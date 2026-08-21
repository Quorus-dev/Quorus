"""Optional Redis persistence for the Phase-1 primitives (WAKE_REBUILD R4).

``persistent_memory_svc``, ``capability_svc``, and ``tool_catalog_svc`` are
asyncio-locked in-memory dicts — correct, fast, and completely amnesiac: a
relay restart wiped every "persistent" memory entry, capability
advertisement, and room tool catalog.

This helper gives them a write-through mirror + lazy hydrate:

* every mutation mirrors the entry into a Redis hash (best-effort — a Redis
  hiccup logs a warning and never fails the caller);
* the first read of a bucket after process start hydrates it from Redis.

No Redis configured → all three helpers are no-ops and the services behave
exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from quorus.backends.redis_client import get_redis_or_none

logger = logging.getLogger("quorus.services.p1_persistence")

_TTL_S = 60 * 60 * 24 * 30  # 30 days — refreshed on read AND write.

# Every other Redis caller in this codebase bounds its awaits
# (redis_backends._with_timeout). These run while a per-bucket asyncio lock
# is held, so an unbounded await on a partitioned-but-not-erroring Redis
# would queue every request for that bucket behind it, forever.
_OP_TIMEOUT_S = float(os.getenv("QUORUS_REDIS_OP_TIMEOUT", "10"))

# How long a hydrated bucket may be trusted before we re-read Redis.
# Hydrating exactly once per process turned the mirror into a cache with
# no invalidation: a second replica that served one read before another
# replica's write never saw that write again (proven 2026-08-21), and
# tool-name uniqueness silently stopped working across replicas.
HYDRATE_TTL_S = float(os.getenv("QUORUS_P1_HYDRATE_TTL", "5"))

# Observability: silent best-effort mirroring hides divergence, so count it.
MIRROR_FAILURES = 0


def _note_failure(op: str, ns_key: str, exc: BaseException) -> None:
    global MIRROR_FAILURES
    MIRROR_FAILURES += 1
    logger.warning(
        "p1 mirror %s failed for %s (%d total): %s",
        op, ns_key, MIRROR_FAILURES, exc,
    )


async def mirror_set(ns_key: str, field: str, entry: dict[str, Any]) -> None:
    r = get_redis_or_none()
    if r is None:
        return
    try:
        async with asyncio.timeout(_OP_TIMEOUT_S):
            pipe = r.pipeline()
            pipe.hset(ns_key, field, json.dumps(entry))
            pipe.expire(ns_key, _TTL_S)
            await pipe.execute()
    except Exception as exc:
        _note_failure("set", ns_key, exc)


async def mirror_delete(ns_key: str, field: str) -> None:
    r = get_redis_or_none()
    if r is None:
        return
    try:
        async with asyncio.timeout(_OP_TIMEOUT_S):
            await r.hdel(ns_key, field)
    except Exception as exc:
        _note_failure("delete", ns_key, exc)


async def hydrate(ns_key: str) -> tuple[dict[str, dict[str, Any]], bool]:
    """Load a mirrored bucket.

    Returns ``(entries, ok)``. ``ok`` distinguishes "there is nothing to
    load" (no Redis configured, or an empty hash — both fine, cache it)
    from "we could not read" (transient error — the caller must NOT mark
    the bucket hydrated, or one blip serves empty state forever).
    """
    r = get_redis_or_none()
    if r is None:
        return {}, True
    try:
        async with asyncio.timeout(_OP_TIMEOUT_S):
            raw = await r.hgetall(ns_key)
            # Touch the TTL on read: refreshing only on write silently
            # expired memory that an agent reads daily but never rewrites.
            if raw:
                await r.expire(ns_key, _TTL_S)
    except Exception as exc:
        _note_failure("hydrate", ns_key, exc)
        return {}, False
    out: dict[str, dict[str, Any]] = {}
    for k, v in (raw or {}).items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        try:
            parsed = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            out[key] = parsed
    return out, True


async def mirror_drop(ns_key: str) -> None:
    """Delete an entire mirrored bucket (used by service reset)."""
    r = get_redis_or_none()
    if r is None:
        return
    try:
        async with asyncio.timeout(_OP_TIMEOUT_S):
            await r.delete(ns_key)
    except Exception as exc:
        _note_failure("drop", ns_key, exc)


class HydrationClock:
    """Bounded staleness tracker for mirrored buckets.

    Replaces a plain ``set`` of "already hydrated" keys, which (a) never
    re-read Redis so replicas diverged permanently, and (b) grew one
    permanent entry per bucket, defeating the LRU bounds it sat beside.
    """

    def __init__(self, max_entries: int = 4096) -> None:
        self._seen: dict[Any, float] = {}
        self._max = max_entries

    def fresh(self, key: Any, ttl: float | None = None) -> bool:
        seen = self._seen.get(key)
        window = HYDRATE_TTL_S if ttl is None else ttl
        return seen is not None and (time.monotonic() - seen) < window

    def mark(self, key: Any) -> None:
        self._seen[key] = time.monotonic()
        while len(self._seen) > self._max:
            oldest = min(self._seen, key=self._seen.get)  # type: ignore[arg-type]
            self._seen.pop(oldest, None)

    def invalidate(self, key: Any) -> None:
        self._seen.pop(key, None)

    def invalidate_where(self, predicate) -> None:
        for key in [k for k in self._seen if predicate(k)]:
            self._seen.pop(key, None)

    def clear(self) -> None:
        self._seen.clear()

    def keys(self) -> list[Any]:
        return list(self._seen)

    def __contains__(self, key: Any) -> bool:  # back-compat with set usage
        return key in self._seen
