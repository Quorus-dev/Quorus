"""Capability discovery service — Plan v8 Phase 1 OS primitive A.

Each agent in a Quorus tenant publishes a capability MANIFEST so peers can
discover who can do what. The service is the server-side registry; routes
in :mod:`quorus.routes.capabilities` expose it over HTTP.

Manifest shape (caller-controlled, validated by the route's Pydantic model):

    {
      "name": "claude-coder-1",
      "description": "writes Python and TypeScript",
      "supported_languages": ["python", "typescript"],
      "supported_frameworks": ["fastapi", "next"],
      "capabilities": ["python", "pytest", "fastapi"]
    }

Storage is in-memory keyed by ``(tenant_id, participant)``. Restart wipes
state — Stream B / Phase 2 owns persistence (same pattern as social_svc).

Public surface:
    * :meth:`publish` — set or replace the manifest for a participant.
    * :meth:`get` — fetch one participant's manifest.
    * :meth:`search` — return all participants whose ``capabilities`` list
      contains every token in the query (substring match, case-insensitive).
    * :meth:`reset` — wipe state (test/admin entry point).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

# Bounds — keep memory honest on long-running replicas. A tenant with
# >5_000 distinct agents publishing manifests is well past Phase 1 scale.
_MAX_MANIFESTS_PER_TENANT = 5_000
_LOCK_LRU_MAX = 1024


class CapabilityError(Exception):
    """Base for service-level rejections (route maps to 4xx)."""


class CapabilitySvc:
    """In-memory capability registry, lock-serialised per tenant."""

    def __init__(self) -> None:
        # { (tid, participant) -> manifest_dict }
        self._manifests: dict[tuple[str, str], dict[str, Any]] = {}
        # Per-tenant lock so concurrent publishes serialise without
        # touching unrelated tenants. LRU-bounded to cap memory.
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._lock_factory_lock = asyncio.Lock()

    async def _get_lock(self, tid: str) -> asyncio.Lock:
        async with self._lock_factory_lock:
            existing = self._locks.get(tid)
            if existing is not None:
                self._locks.move_to_end(tid)
                return existing
            new_lock = asyncio.Lock()
            self._locks[tid] = new_lock
            while len(self._locks) > _LOCK_LRU_MAX:
                self._locks.popitem(last=False)
            return new_lock

    async def publish(
        self,
        tid: str,
        participant: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Set or replace the manifest for ``participant`` in ``tid``."""
        if not participant:
            raise CapabilityError("participant required")
        if not isinstance(manifest, dict):
            raise CapabilityError("manifest must be a dict")
        lock = await self._get_lock(tid)
        async with lock:
            tenant_count = sum(
                1 for (t, _p) in self._manifests if t == tid
            )
            existing = self._manifests.get((tid, participant))
            if existing is None and tenant_count >= _MAX_MANIFESTS_PER_TENANT:
                raise CapabilityError(
                    f"tenant {tid!r} reached manifest cap "
                    f"({_MAX_MANIFESTS_PER_TENANT})"
                )
            stored = dict(manifest)
            stored["participant"] = participant
            stored["updated_at"] = time.time()
            self._manifests[(tid, participant)] = stored
            return dict(stored)

    async def get(
        self, tid: str, participant: str,
    ) -> dict[str, Any] | None:
        lock = await self._get_lock(tid)
        async with lock:
            m = self._manifests.get((tid, participant))
            return dict(m) if m else None

    async def search(
        self, tid: str, has: list[str],
    ) -> list[dict[str, Any]]:
        """Return manifests where every token in ``has`` matches a
        capability (case-insensitive substring). Empty ``has`` returns
        all manifests for the tenant."""
        wanted = [t.lower().strip() for t in (has or []) if t.strip()]
        lock = await self._get_lock(tid)
        async with lock:
            results: list[dict[str, Any]] = []
            for (t, _p), manifest in self._manifests.items():
                if t != tid:
                    continue
                caps_raw = manifest.get("capabilities") or []
                caps_lower = [
                    str(c).lower() for c in caps_raw if isinstance(c, str)
                ]
                if all(
                    any(token in c for c in caps_lower) for token in wanted
                ):
                    results.append(dict(manifest))
            results.sort(key=lambda m: m.get("participant", ""))
            return results

    async def reset(self, tid: str | None = None) -> None:
        """Wipe state. No tid → full wipe; with tid → that tenant only."""
        async with self._lock_factory_lock:
            if tid is None:
                self._manifests.clear()
                self._locks.clear()
                return
            self._manifests = {
                k: v for k, v in self._manifests.items() if k[0] != tid
            }
            self._locks.pop(tid, None)


__all__ = ["CapabilitySvc", "CapabilityError"]
