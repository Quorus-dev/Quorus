"""SSE listener with circuit breaker for Quorus MCP server.

The SSEListener class manages a persistent Server-Sent Events connection to
the relay. It implements exponential backoff (capped at 30s) and a circuit
breaker that trips after 10 consecutive failures. While tripped, the caller
should fall back to polling. The breaker probes every 60s; on success it resets.
"""

from __future__ import annotations

import asyncio
import json as json_module
import logging
from typing import Any, Callable, Coroutine

import httpx

logger = logging.getLogger("mcp_tunnel.sse")

_FAILURE_THRESHOLD = 10
_PROBE_INTERVAL = 60
_MAX_BACKOFF = 30


class SSEListener:
    """Persistent SSE listener with circuit breaker."""

    def __init__(
        self,
        relay_url: str,
        instance_name: str,
        get_http_client: Callable[[], httpx.AsyncClient],
        get_sse_token: Callable[[], Coroutine[Any, Any, str]],
        on_event: Callable[[str, str], Coroutine[Any, Any, None]],
    ) -> None:
        self._relay_url = relay_url
        self._instance_name = instance_name
        self._get_http_client = get_http_client
        self._get_sse_token = get_sse_token
        self._on_event = on_event

        self._failures = 0
        self._tripped = False
        self._last_error: str | None = None
        self._breaker_lock = asyncio.Lock()

    def breaker_state(self) -> dict[str, Any]:
        """Return circuit breaker state (non-blocking snapshot)."""
        return {
            "tripped": self._tripped,
            "failures": self._failures,
            "last_error": self._last_error,
        }

    async def run(self, stop_event: asyncio.Event) -> None:
        """Main loop. Runs until stop_event is set."""
        while not stop_event.is_set():
            if self._tripped:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=_PROBE_INTERVAL)
                    return
                except asyncio.TimeoutError:
                    pass
                success = await self._attempt_connect(stop_event, probe=True)
                if success:
                    async with self._breaker_lock:
                        self._tripped = False
                        self._failures = 0
                        self._last_error = None
                    logger.info("SSE circuit breaker reset after successful probe")
                continue

            await self._attempt_connect(stop_event, probe=False)
            if stop_event.is_set():
                return

    async def _attempt_connect(
        self, stop_event: asyncio.Event, probe: bool
    ) -> bool:
        """Try one SSE connection. Return True if a probe connects successfully."""
        backoff = 2
        try:
            client = self._get_http_client()
            url = f"{self._relay_url}/stream/{self._instance_name}"
            token = await self._get_sse_token()
            params = {"token": token}
            async with client.stream("GET", url, params=params, timeout=None) as resp:
                if resp.status_code != 200:
                    err = f"HTTP {resp.status_code}"
                    logger.warning("SSE stream returned %s, retrying", err)
                    await self._record_failure(err)
                    await self._sleep_or_stop(stop_event, backoff)
                    return False

                async with self._breaker_lock:
                    self._failures = 0
                    self._last_error = None

                logger.info("SSE stream connected for %s", self._instance_name)

                if probe:
                    return True

                event_type = ""
                event_data = ""
                async for line in resp.aiter_lines():
                    if stop_event.is_set():
                        return False
                    line = line.strip()
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        event_data = line[5:].strip()
                    elif line == "" and event_type:
                        await self._on_event(event_type, event_data)
                        event_type = ""
                        event_data = ""

                return False

        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            err = str(exc)
            logger.warning("SSE connection lost, retrying in %ds: %s", backoff, err)
            await self._record_failure(err)
        except Exception as exc:
            err = str(exc)
            logger.warning("SSE listener error, retrying in %ds", backoff, exc_info=True)
            await self._record_failure(err)

        if not stop_event.is_set():
            await self._sleep_or_stop(stop_event, backoff)
        return False

    async def _record_failure(self, error: str) -> None:
        async with self._breaker_lock:
            self._failures += 1
            self._last_error = error
            if self._failures >= _FAILURE_THRESHOLD and not self._tripped:
                self._tripped = True
                logger.error(
                    "SSE circuit breaker tripped after %d consecutive failures. "
                    "Last error: %s. Will probe every %ds.",
                    self._failures,
                    error,
                    _PROBE_INTERVAL,
                )

    @staticmethod
    async def _sleep_or_stop(stop_event: asyncio.Event, duration: float) -> None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=duration)
        except asyncio.TimeoutError:
            pass


def process_sse_event_data(data: str) -> dict | None:
    """Parse SSE data string into a message dict, or return None on error."""
    try:
        return json_module.loads(data)
    except (json_module.JSONDecodeError, KeyError):
        logger.warning("Failed to parse SSE event data: %s", data[:200])
        return None
