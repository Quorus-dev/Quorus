"""Tool catalog service — Plan v8 Phase 1 OS primitive B.

Each ROOM has a registered list of MCP servers (name + url + access policy).
Agents in the room call ``GET /v1/rooms/{rid}/tools`` to discover what
tools are available. Registration is room-admin-only; reads are
member-gated by the route layer.

Storage is in-memory keyed by ``(tenant_id, room_id)``. State is dropped
when the room is deleted or via :meth:`reset`. Phase 2 may persist to
Postgres alongside room state.

Tool record shape (route-validated):

    {
      "name": "github-mcp",
      "url": "https://mcp.example.com",
      "access": "room",       # "room" | "tenant" | "public"
      "description": "GitHub repo + PR ops",
      "registered_by": "alice",
      "registered_at": 1730000000.0
    }

Public surface:
    * :meth:`register` — add a new tool to a room (rejects duplicates).
    * :meth:`list` — snapshot of all tools registered in the room.
    * :meth:`get` — single record, or ``None`` if absent.
    * :meth:`remove` — delete by name; idempotent (returns ``False``
      if not found, ``True`` if deleted).
    * :meth:`reset` — wipe (test entry point).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

from quorus.services import p1_persistence as _p1

# Bounds — protect against runaway registration. 256 distinct MCP
# servers per room is well past Phase 1 needs (typical: 5–20).
_MAX_TOOLS_PER_ROOM = 256
_LOCK_LRU_MAX = 2048


class ToolCatalogError(Exception):
    """Base for service rejections (route maps to 4xx)."""


class DuplicateToolError(ToolCatalogError):
    """Raised when a tool name already exists in the room (HTTP 409)."""


class ToolNotFoundError(ToolCatalogError):
    """Raised when a tool name isn't registered (HTTP 404)."""


class ToolCatalogSvc:
    """In-memory MCP server registry, scoped per (tenant, room)."""

    def __init__(self) -> None:
        self._hydrated = _p1.HydrationClock()
        # { (tid, rid) -> { tool_name -> record } }
        self._tools: dict[
            tuple[str, str], dict[str, dict[str, Any]]
        ] = {}
        self._locks: OrderedDict[
            tuple[str, str], asyncio.Lock
        ] = OrderedDict()
        self._lock_factory_lock = asyncio.Lock()

    async def _get_lock(self, tid: str, rid: str) -> asyncio.Lock:
        key = (tid, rid)
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
        self, tid: str, rid: str,
    ) -> dict[str, dict[str, Any]]:
        return self._tools.setdefault((tid, rid), {})

    @staticmethod
    def _ns(tid: str, rid: str) -> str:
        return f"p1tools:{tid}:{rid}"

    async def _hydrate_once(self, tid: str, rid: str, *, force: bool = False) -> None:
        """R4: first touch of a room catalog after start loads the Redis
        mirror. Caller must hold the room lock."""
        key = (tid, rid)
        if self._hydrated.fresh(key, ttl=0.0 if force else None):
            return
        stored, ok = await _p1.hydrate(self._ns(tid, rid))
        if not ok:
            return  # transient failure — retry on the next read
        self._hydrated.mark(key)
        if stored:
            bucket = self._tools.setdefault(key, {})
            for name, record in stored.items():
                bucket.setdefault(name, record)

    async def register(
        self,
        tid: str,
        rid: str,
        *,
        name: str,
        url: str,
        registered_by: str,
        access: str = "room",
        description: str = "",
    ) -> dict[str, Any]:
        """Register a new tool. Raises :class:`DuplicateToolError`
        on name collision in the same room."""
        if not name.strip():
            raise ToolCatalogError("name required")
        if not url.strip():
            raise ToolCatalogError("url required")
        access_norm = access.lower().strip()
        if access_norm not in {"room", "tenant", "public"}:
            raise ToolCatalogError(
                f"access must be room|tenant|public, got {access!r}"
            )
        lock = await self._get_lock(tid, rid)
        async with lock:
            # Uniqueness must be decided against the SHARED catalog, not a
            # possibly-stale local cache: a replica that had not seen a
            # peer's registration accepted the same name and its mirror
            # write silently overwrote the peer's record.
            await self._hydrate_once(tid, rid, force=True)
            bucket = self._bucket(tid, rid)
            if name in bucket:
                raise DuplicateToolError(
                    f"tool {name!r} already registered in this room"
                )
            if len(bucket) >= _MAX_TOOLS_PER_ROOM:
                raise ToolCatalogError(
                    f"room reached tool cap ({_MAX_TOOLS_PER_ROOM})"
                )
            record = {
                "name": name,
                "url": url,
                "access": access_norm,
                "description": description[:500],
                "registered_by": registered_by,
                "registered_at": time.time(),
            }
            bucket[name] = record
            await _p1.mirror_set(self._ns(tid, rid), name, record)
            return dict(record)

    async def list(
        self, tid: str, rid: str,
    ) -> list[dict[str, Any]]:
        lock = await self._get_lock(tid, rid)
        async with lock:
            await self._hydrate_once(tid, rid)
            items = [dict(r) for r in self._bucket(tid, rid).values()]
        items.sort(key=lambda r: r.get("name", ""))
        return items

    async def get(
        self, tid: str, rid: str, name: str,
    ) -> dict[str, Any] | None:
        lock = await self._get_lock(tid, rid)
        async with lock:
            await self._hydrate_once(tid, rid)
            r = self._bucket(tid, rid).get(name)
            return dict(r) if r else None

    async def remove(
        self, tid: str, rid: str, name: str,
    ) -> bool:
        lock = await self._get_lock(tid, rid)
        async with lock:
            await self._hydrate_once(tid, rid)
            removed = self._bucket(tid, rid).pop(name, None)
            if removed is not None:
                await _p1.mirror_delete(self._ns(tid, rid), name)
            return removed is not None

    async def reset(
        self, tid: str | None = None, rid: str | None = None,
    ) -> None:
        # Mirror keys to drop, resolved before we mutate the hydrated set.
        if tid is None and rid is None:
            doomed = self._hydrated.keys()
        elif rid is None:
            doomed = [k for k in self._hydrated.keys() if k[0] == tid]
        else:
            doomed = [(tid, rid)]
        async with self._lock_factory_lock:
            if tid is None and rid is None:
                self._tools.clear()
                self._locks.clear()
                self._hydrated.clear()
            elif rid is None:
                self._tools = {
                    k: v for k, v in self._tools.items() if k[0] != tid
                }
                self._locks = OrderedDict(
                    (k, lk) for k, lk in self._locks.items() if k[0] != tid
                )
                self._hydrated.invalidate_where(lambda k: k[0] == tid)
            else:
                self._tools.pop((tid, rid), None)
                self._locks.pop((tid, rid), None)
                self._hydrated.invalidate((tid, rid))
        # Drop the mirror too — otherwise reset data returns on restart.
        for t, r in doomed:
            await _p1.mirror_drop(self._ns(t, r))


__all__ = [
    "ToolCatalogSvc",
    "ToolCatalogError",
    "DuplicateToolError",
    "ToolNotFoundError",
]
