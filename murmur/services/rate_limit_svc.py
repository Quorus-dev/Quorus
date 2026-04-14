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

    async def check_with_limit(
        self,
        tenant_id: str,
        sender: str,
        max_count: int,
        window: int | None = None,
    ) -> bool:
        """Like ``check`` but with a caller-specified max_count and optional window.

        Pass ``window`` (in seconds) when an endpoint needs a different cadence
        than the default — e.g. signup uses 3600s (5 attempts per hour per IP),
        while message sends use the service's default short window.
        """
        return await self._backend.check_and_increment(
            tenant_id, sender, window if window is not None else self._window, max_count
        )
