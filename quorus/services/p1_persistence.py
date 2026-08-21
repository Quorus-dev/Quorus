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

import json
import logging
from typing import Any

from quorus.backends.redis_client import get_redis_or_none

logger = logging.getLogger("quorus.services.p1_persistence")

_TTL_S = 60 * 60 * 24 * 30  # 30 days — refreshed on every write.


async def mirror_set(ns_key: str, field: str, entry: dict[str, Any]) -> None:
    r = get_redis_or_none()
    if r is None:
        return
    try:
        await r.hset(ns_key, field, json.dumps(entry))
        await r.expire(ns_key, _TTL_S)
    except Exception as exc:
        logger.warning("p1 mirror set failed for %s: %s", ns_key, exc)


async def mirror_delete(ns_key: str, field: str) -> None:
    r = get_redis_or_none()
    if r is None:
        return
    try:
        await r.hdel(ns_key, field)
    except Exception as exc:
        logger.warning("p1 mirror delete failed for %s: %s", ns_key, exc)


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
        raw = await r.hgetall(ns_key)
    except Exception as exc:
        logger.warning("p1 hydrate failed for %s: %s", ns_key, exc)
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
        await r.delete(ns_key)
    except Exception as exc:
        logger.warning("p1 mirror drop failed for %s: %s", ns_key, exc)
