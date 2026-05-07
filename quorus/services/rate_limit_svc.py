"""Rate-limiting service wrapping a RateLimitBackend."""

from __future__ import annotations

import structlog

from quorus.backends.protocol import RateLimitBackend

logger = structlog.get_logger("quorus.services.rate_limit")


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
        try:
            return await self._backend.check_and_increment(
                tenant_id, sender, self._window, self._max_count
            )
        except Exception as exc:
            logger.warning(
                "rate_limit_backend_failed_open",
                op="check",
                tenant_id=tenant_id,
                sender=sender,
                error=str(exc),
            )
            return True

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
        effective_window = window if window is not None else self._window
        try:
            return await self._backend.check_and_increment(
                tenant_id, sender, effective_window, max_count
            )
        except Exception as exc:
            logger.warning(
                "rate_limit_backend_failed_open",
                op="check_with_limit",
                tenant_id=tenant_id,
                sender=sender,
                window=effective_window,
                max_count=max_count,
                error=str(exc),
            )
            return True

    async def is_rate_limited(
        self,
        tenant_id: str,
        sender: str,
        max_count: int,
        window: int | None = None,
    ) -> bool:
        """Check if sender is rate limited (read-only, does not increment)."""
        effective_window = window if window is not None else self._window
        try:
            return await self._backend.is_rate_limited(
                tenant_id, sender, effective_window, max_count
            )
        except Exception as exc:
            logger.warning(
                "rate_limit_backend_failed_open",
                op="is_rate_limited",
                tenant_id=tenant_id,
                sender=sender,
                window=effective_window,
                max_count=max_count,
                error=str(exc),
            )
            return False

    async def record(
        self,
        tenant_id: str,
        sender: str,
        window: int | None = None,
    ) -> None:
        """Record an event without checking the limit."""
        effective_window = window if window is not None else self._window
        try:
            await self._backend.record(
                tenant_id, sender, effective_window
            )
        except Exception as exc:
            logger.warning(
                "rate_limit_backend_failed_open",
                op="record",
                tenant_id=tenant_id,
                sender=sender,
                window=effective_window,
                error=str(exc),
            )
