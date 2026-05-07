"""Persistent memory service — Plan v8 Phase 1 OS primitive C.

Each agent gets a per-room key/value store the relay holds for them. Unlike
:mod:`quorus.runtime.memory` (on-host JSONL), this is a SERVER-SIDE store
queryable by other room members (with permission). That's the difference
that makes it an OS primitive: agents in the same room can read each
other's published memory entries.

Visibility:
    * ``private`` — only the owning participant can read/write.
    * ``room``    — any room member may read; only owner may write.
    * ``public``  — any tenant member may read; only owner may write.

Storage is in-memory keyed by ``(tid, participant, room_id)`` → ``{key: entry}``.
Phase 2 may persist to a Postgres table ``agent_memory(participant, room_id,
key, value, updated_at, visibility)`` — the service interface stays stable.

Bounds:
    * Value size capped at ``MAX_VALUE_BYTES`` (16 KiB).
    * Per (participant, room) entry count capped at ``MAX_ENTRIES``.

Public surface:
    * :meth:`set` — write a value.
    * :meth:`get` — read by key.
    * :meth:`list` — snapshot of all keys for (participant, room).
    * :meth:`delete` — GDPR-compliant removal.
    * :meth:`reset` — wipe (test entry point).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any

MAX_VALUE_BYTES = 16 * 1024
MAX_ENTRIES = 1024
MAX_KEY_LEN = 200

VISIBILITY_PRIVATE = "private"
VISIBILITY_ROOM = "room"
VISIBILITY_PUBLIC = "public"
_VALID_VISIBILITY = frozenset(
    {VISIBILITY_PRIVATE, VISIBILITY_ROOM, VISIBILITY_PUBLIC}
)

_LOCK_LRU_MAX = 2048


class MemoryError_(Exception):
    """Base for service rejections (route maps to 4xx)."""


class ValueTooLargeError(MemoryError_):
    """Raised when serialized value exceeds :data:`MAX_VALUE_BYTES`."""


class EntryCapError(MemoryError_):
    """Raised when (participant, room) hits :data:`MAX_ENTRIES`."""


class PersistentMemorySvc:
    """In-memory persistent KV store, scoped per (tenant, participant, room)."""

    def __init__(self) -> None:
        # { (tid, participant, rid) -> { key -> entry } }
        self._entries: dict[
            tuple[str, str, str], dict[str, dict[str, Any]]
        ] = {}
        self._locks: OrderedDict[
            tuple[str, str, str], asyncio.Lock
        ] = OrderedDict()
        self._lock_factory_lock = asyncio.Lock()

    async def _get_lock(
        self, tid: str, participant: str, rid: str,
    ) -> asyncio.Lock:
        key = (tid, participant, rid)
        async with self._lock_factory_lock:
            existing = self._locks.get(key)
            if existing is not None:
                self._locks.move_to_end(key)
                return existing
            new_lock = asyncio.Lock()
            self._locks[key] = new_lock
            while len(self._locks) > _LOCK_LRU_MAX:
                self._locks.popitem(last=False)
            return new_lock

    def _bucket(
        self, tid: str, participant: str, rid: str,
    ) -> dict[str, dict[str, Any]]:
        return self._entries.setdefault((tid, participant, rid), {})

    @staticmethod
    def _serialize_size(value: Any) -> int:
        try:
            return len(json.dumps(value, separators=(",", ":")))
        except (TypeError, ValueError):
            # Non-JSON-serialisable — refuse upstream
            raise MemoryError_("value must be JSON-serialisable")

    async def set(
        self,
        tid: str,
        participant: str,
        rid: str,
        key: str,
        value: Any,
        *,
        visibility: str = VISIBILITY_PRIVATE,
    ) -> dict[str, Any]:
        if not key or len(key) > MAX_KEY_LEN:
            raise MemoryError_(
                f"key required and ≤{MAX_KEY_LEN} chars"
            )
        vis = visibility.lower().strip()
        if vis not in _VALID_VISIBILITY:
            raise MemoryError_(
                f"visibility must be private|room|public, got {visibility!r}"
            )
        size = self._serialize_size(value)
        if size > MAX_VALUE_BYTES:
            raise ValueTooLargeError(
                f"value size {size}B exceeds cap {MAX_VALUE_BYTES}B"
            )
        lock = await self._get_lock(tid, participant, rid)
        async with lock:
            bucket = self._bucket(tid, participant, rid)
            if key not in bucket and len(bucket) >= MAX_ENTRIES:
                raise EntryCapError(
                    f"entry cap reached ({MAX_ENTRIES}) for "
                    f"{participant!r}/{rid!r}"
                )
            entry = {
                "key": key,
                "value": value,
                "visibility": vis,
                "updated_at": time.time(),
                "size_bytes": size,
            }
            bucket[key] = entry
            return dict(entry)

    async def get(
        self,
        tid: str,
        participant: str,
        rid: str,
        key: str,
    ) -> dict[str, Any] | None:
        lock = await self._get_lock(tid, participant, rid)
        async with lock:
            entry = self._bucket(tid, participant, rid).get(key)
            return dict(entry) if entry else None

    async def list(
        self,
        tid: str,
        participant: str,
        rid: str,
        *,
        visibility_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        lock = await self._get_lock(tid, participant, rid)
        async with lock:
            entries = [
                dict(e) for e in self._bucket(tid, participant, rid).values()
            ]
        if visibility_filter:
            vf = visibility_filter.lower().strip()
            entries = [e for e in entries if e.get("visibility") == vf]
        entries.sort(key=lambda e: e.get("key", ""))
        return entries

    async def delete(
        self,
        tid: str,
        participant: str,
        rid: str,
        key: str,
    ) -> bool:
        lock = await self._get_lock(tid, participant, rid)
        async with lock:
            removed = self._bucket(tid, participant, rid).pop(key, None)
            return removed is not None

    async def reset(self, tid: str | None = None) -> None:
        async with self._lock_factory_lock:
            if tid is None:
                self._entries.clear()
                self._locks.clear()
                return
            self._entries = {
                k: v for k, v in self._entries.items() if k[0] != tid
            }
            self._locks = OrderedDict(
                (k, lk) for k, lk in self._locks.items() if k[0] != tid
            )


__all__ = [
    "PersistentMemorySvc",
    "MemoryError_",
    "ValueTooLargeError",
    "EntryCapError",
    "MAX_VALUE_BYTES",
    "MAX_ENTRIES",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_ROOM",
    "VISIBILITY_PUBLIC",
]
