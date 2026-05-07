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
            return dict(record)

    async def list(
        self, tid: str, rid: str,
    ) -> list[dict[str, Any]]:
        lock = await self._get_lock(tid, rid)
        async with lock:
            items = [dict(r) for r in self._bucket(tid, rid).values()]
        items.sort(key=lambda r: r.get("name", ""))
        return items

    async def get(
        self, tid: str, rid: str, name: str,
    ) -> dict[str, Any] | None:
        lock = await self._get_lock(tid, rid)
        async with lock:
            r = self._bucket(tid, rid).get(name)
            return dict(r) if r else None

    async def remove(
        self, tid: str, rid: str, name: str,
    ) -> bool:
        lock = await self._get_lock(tid, rid)
        async with lock:
            removed = self._bucket(tid, rid).pop(name, None)
            return removed is not None

    async def reset(
        self, tid: str | None = None, rid: str | None = None,
    ) -> None:
        async with self._lock_factory_lock:
            if tid is None and rid is None:
                self._tools.clear()
                self._locks.clear()
                return
            if rid is None:
                self._tools = {
                    k: v for k, v in self._tools.items() if k[0] != tid
                }
                self._locks = OrderedDict(
                    (k, lk) for k, lk in self._locks.items() if k[0] != tid
                )
                return
            self._tools.pop((tid, rid), None)
            self._locks.pop((tid, rid), None)


__all__ = [
    "ToolCatalogSvc",
    "ToolCatalogError",
    "DuplicateToolError",
    "ToolNotFoundError",
]
