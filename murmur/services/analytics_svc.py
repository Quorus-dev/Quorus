"""Analytics tracking service wrapping an AnalyticsBackend."""

from __future__ import annotations

from murmur.backends.protocol import AnalyticsBackend


class AnalyticsService:
    """Thin wrapper that delegates send/delivery tracking to a backend."""

    def __init__(self, backend: AnalyticsBackend) -> None:
        self._backend = backend

    async def track_send(self, tenant_id: str, sender: str) -> None:
        """Record that *sender* sent a message."""
        await self._backend.track_send(tenant_id, sender)

    async def track_delivery(
        self, tenant_id: str, recipient: str, count: int
    ) -> None:
        """Record that *count* messages were delivered to *recipient*."""
        if count > 0:
            await self._backend.track_delivery(tenant_id, recipient, count)

    async def get_stats(self, tenant_id: str) -> dict:
        """Return aggregate analytics for *tenant_id*."""
        return await self._backend.get_stats(tenant_id)
