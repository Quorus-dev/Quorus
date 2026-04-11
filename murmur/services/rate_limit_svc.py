"""Rate-limiting service wrapping a RateLimitBackend."""

from __future__ import annotations

from murmur.backends.protocol import RateLimitBackend


class RateLimitService:
    """Sliding-window rate limiter delegating to a backend."""

    def __init__(
        self,
        backend: RateLimitBackend,
        window: int = 60,
        max_count: int = 60,
    ) -> None:
        self._backend = backend
        self._window = window
        self._max_count = max_count

    async def check(self, tenant_id: str, sender: str) -> bool:
        """Return ``True`` if the request is allowed, ``False`` if rate-limited."""
        return await self._backend.check_and_increment(
            tenant_id, sender, self._window, self._max_count
        )
