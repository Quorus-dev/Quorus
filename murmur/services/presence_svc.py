"""Presence / heartbeat service wrapping a PresenceBackend."""

from __future__ import annotations

from murmur.backends.protocol import PresenceBackend


class PresenceService:
    """Agent presence tracking via periodic heartbeats."""

    def __init__(self, backend: PresenceBackend) -> None:
        self._backend = backend

    async def heartbeat(
        self,
        tenant_id: str,
        name: str,
        status: str,
        room: str,
    ) -> dict:
        """Record a heartbeat for *name* and return the stored entry."""
        return await self._backend.heartbeat(tenant_id, name, status, room)

    async def list_all(
        self, tenant_id: str, timeout: int
    ) -> list[dict]:
        """Return all participants whose last heartbeat is within *timeout* seconds."""
        return await self._backend.list_all(tenant_id, timeout)
