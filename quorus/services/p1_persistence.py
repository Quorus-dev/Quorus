"""Optional Redis persistence for the Phase-1 primitives (WAKE_REBUILD R4).

``persistent_memory_svc``, ``capability_svc``, and ``tool_catalog_svc`` are
asyncio-locked in-memory dicts — correct, fast, and completely amnesiac: a
relay restart wiped every "persistent" memory entry, capability
advertisement, and room tool catalog.

This helper gives them a write-through mirror + lazy hydrate:

* every mutation mirrors the entry into a Redis hash (best-effort — a Redis
  hiccup logs a warning and never fails the caller);
* the first read of a bucket after process start hydrates it from Redis.

No Redis configured → the same mirror goes to a stdlib SQLite file
(``p1_sqlite``) next to the relay's other state, so a single-node relay keeps
them across a restart too (memory slice 1). Redis stays the first choice when
it is configured.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from typing import Any

from redis.exceptions import WatchError

from quorus.backends.redis_client import get_redis_or_none
from quorus.services import p1_sqlite

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



if sys.version_info >= (3, 11):
    _timeout = asyncio.timeout
else:  # Python 3.10: asyncio.timeout doesn't exist. Every mirror op raised
    # AttributeError there, so nothing ever persisted (found 2026-10-09).
    @contextlib.asynccontextmanager
    async def _timeout(delay: float):  # type: ignore[no-redef]
        task = asyncio.current_task()
        fired = False

        def _fire() -> None:
            nonlocal fired
            fired = True
            if task is not None:
                task.cancel()

        handle = asyncio.get_running_loop().call_later(delay, _fire)
        try:
            yield
        except asyncio.CancelledError as exc:
            if fired:
                raise TimeoutError(f"operation exceeded {delay}s") from exc
            raise
        finally:
            handle.cancel()

def _note_failure(op: str, ns_key: str, exc: BaseException) -> None:
    global MIRROR_FAILURES
    MIRROR_FAILURES += 1
    logger.warning(
        "p1 mirror %s failed for %s (%d total): %s",
        op, ns_key, MIRROR_FAILURES, exc,
    )


async def _local(op: str, ns_key: str, fn, *args) -> tuple[Any, bool]:
    """Run a blocking ``p1_sqlite`` call off the event loop. Returns
    ``(result, ok)``. A corrupt store is not ok but isn't counted again (it
    was logged once): the services keep serving what they hold in memory."""
    try:
        return await asyncio.to_thread(fn, ns_key, *args), True
    except p1_sqlite.Unavailable:
        return None, False
    except Exception as exc:  # sqlite3 errors, a full disk, a bad mode
        _note_failure(op, ns_key, exc)
        return None, False


_CAS_TRIES = 8


async def _redis_put(r, ns_key: str, field: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Compare-and-set on the Redis hash: write *entry* unless the field holds
    a newer write, as a WATCH/MULTI transaction (no Lua, so it runs anywhere,
    fakeredis included). Returns what the hash holds afterwards."""
    mine = p1_sqlite.written_at(entry)
    for _ in range(_CAS_TRIES):
        async with r.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(ns_key)
                raw = await pipe.hget(ns_key, field)
                theirs = _parse(raw)
                if theirs is not None and p1_sqlite.written_at(theirs) > mine:
                    await pipe.unwatch()
                    return theirs
                pipe.multi()
                pipe.hset(ns_key, field, json.dumps(entry))
                pipe.expire(ns_key, _TTL_S)
                await pipe.execute()
                return entry
            except WatchError:
                continue  # someone wrote the hash between our read and write
    raise RuntimeError(f"p1 mirror: {field!r} kept changing under us")


def _parse(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def _put(ns_key: str, field: str, entry: dict[str, Any],
               op: str = "set") -> tuple[bool, dict[str, Any] | None]:
    """Write *entry* unless the store holds a newer write to the field.
    Returns ``(ok, winner)``: what the store holds afterwards (ours or the
    newer one), or ``(False, None)`` when the store couldn't be reached."""
    r = get_redis_or_none()
    if r is None:
        winner, ok = await _local(op, ns_key, p1_sqlite.set_entry, field, entry)
        return ok, winner
    try:
        async with _timeout(_OP_TIMEOUT_S):
            return True, await _redis_put(r, ns_key, field, entry)
    except Exception as exc:
        _note_failure(op, ns_key, exc)
        return False, None


async def mirror_set(ns_key: str, field: str, entry: dict[str, Any]) -> bool:
    """Mirror one entry (never over a newer one). False when the store
    didn't take it."""
    return (await _put(ns_key, field, entry))[0]


def tombstone(at: float | None = None) -> dict[str, Any]:
    """What a delete leaves in the store: no content, only when it happened.
    Without it another relay process can't tell a delete from "never had it",
    and re-wrote an older missed set over the delete (claude, fbcfa2dc)."""
    return {"deleted": True, "updated_at": time.time() if at is None else at}


def is_tombstone(entry: dict[str, Any] | None) -> bool:
    return bool(entry) and entry.get("deleted") is True


async def mirror_delete(ns_key: str, field: str, at: float | None = None) -> bool:
    """Replace one mirrored entry with a tombstone (its content is gone),
    unless the store holds a newer write. False when the store didn't take it."""
    return (await _put(ns_key, field, tombstone(at), "delete"))[0]


async def hydrate(ns_key: str) -> tuple[dict[str, dict[str, Any]], bool]:
    """Load a mirrored bucket.

    Returns ``(entries, ok)``. When ``ok`` the entries are the whole bucket
    as the store has it, delete tombstones included (an empty dict means
    empty): callers replace their cache with it via
    :meth:`PendingWrites.reconcile`, which never serves a tombstone. Not ``ok`` means "we
    could not read" (a blip, a locked or corrupt file): the caller keeps what
    it holds and must NOT mark the bucket hydrated, or one blip serves empty
    state forever.
    """
    r = get_redis_or_none()
    if r is None:
        stored, ok = await _local("hydrate", ns_key, p1_sqlite.load)
        return (stored or {}), ok
    try:
        async with _timeout(_OP_TIMEOUT_S):
            raw = await r.hgetall(ns_key)
            # Touch the TTL on read: refreshing only on write silently
            # expired memory that an agent reads daily but never rewrites.
            if raw:
                await r.expire(ns_key, _TTL_S)
    except Exception as exc:
        _note_failure("hydrate", ns_key, exc)
        return {}, False
    out: dict[str, dict[str, Any]] = {}
    expired: list[tuple[str, str]] = []
    for k, v in (raw or {}).items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        try:
            parsed = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        if is_tombstone(parsed) and float(parsed.get("updated_at") or 0) < time.time() - _TTL_S:
            expired.append((key, val))
            continue
        out[key] = parsed
    for field, observed in expired:
        try:
            async with _timeout(_OP_TIMEOUT_S):
                await _redis_prune(r, ns_key, field, observed)
        except Exception as exc:  # pruning is housekeeping; serve the read anyway
            _note_failure("prune", ns_key, exc)
    return out, True


async def _redis_prune(r, ns_key: str, field: str, observed: str) -> None:
    """Delete *field* only while it still holds the tombstone we read: a write
    that replaced it after our read must survive (codex, review of a86d9b76)."""
    async with r.pipeline(transaction=True) as pipe:
        try:
            await pipe.watch(ns_key)
            raw = await pipe.hget(ns_key, field)
            current = raw.decode() if isinstance(raw, bytes) else raw
            if current != observed:
                await pipe.unwatch()
                return
            pipe.multi()
            pipe.hdel(ns_key, field)
            await pipe.execute()
        except WatchError:
            return  # it changed under us: leave it


async def mirror_drop(ns_key: str) -> None:
    """Delete an entire mirrored bucket (used by service reset)."""
    r = get_redis_or_none()
    if r is None:
        await _local("drop", ns_key, p1_sqlite.drop)
        return
    try:
        async with _timeout(_OP_TIMEOUT_S):
            await r.delete(ns_key)
    except Exception as exc:
        _note_failure("drop", ns_key, exc)


class PendingWrites:
    """Writes this process made that the store didn't take (a locked file, a
    Redis blip), per bucket.

    A successful hydrate is the authoritative bucket: merging it into the
    cache with ``setdefault`` kept entries another relay process had deleted
    and kept an entry's old visibility after it changed (codex, 2844ce20). So
    a refresh replaces the cache, except for these writes, which are kept and
    replayed until the store has them. The replay is the store's own
    compare-and-set, so a newer write or delete from another process wins even
    if it lands after the snapshot was read (claude 8bfb268d, codex 6d825f38).
    """

    def __init__(self) -> None:
        # bucket -> field -> (op, when the write was made)
        self._ops: dict[Any, dict[str, tuple[str, float]]] = {}

    def note(self, bucket: Any, field: str, op: str, mirrored: bool,
             at: float | None = None) -> None:
        ops = self._ops.setdefault(bucket, {})
        if mirrored:
            ops.pop(field, None)
        else:
            ops[field] = (op, time.time() if at is None else at)
        if not ops:
            self._ops.pop(bucket, None)

    async def reconcile(self, bucket: Any, ns_key: str,
                        cached: dict[str, dict[str, Any]],
                        stored: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """The bucket to serve after a successful hydrate of *ns_key*: the live
        stored entries, with each missed local write replayed through the
        store's compare-and-set. Whatever wins there (ours, or a newer write or
        delete that landed after the snapshot) is what gets served."""
        merged = {f: e for f, e in stored.items() if not is_tombstone(e)}
        for field, (op, at) in list(self._ops.get(bucket, {}).items()):
            if op == "set" and field in cached:
                ok, winner = await _put(ns_key, field, cached[field])
            elif op == "delete":
                ok, winner = await _put(ns_key, field, tombstone(at), "delete")
            else:
                self.note(bucket, field, op, True)
                continue
            if not ok:  # still unreachable: serve our write, retry next time
                if op == "set":
                    merged[field] = cached[field]
                else:
                    merged.pop(field, None)
                self.note(bucket, field, op, False, at)
                continue
            self.note(bucket, field, op, True)
            if winner is None or is_tombstone(winner):
                merged.pop(field, None)
            else:
                merged[field] = winner
        return merged

    def forget(self, predicate=None) -> None:
        """Drop pending writes for buckets matching *predicate* (all if None)."""
        for bucket in [b for b in self._ops if predicate is None or predicate(b)]:
            self._ops.pop(bucket, None)


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
