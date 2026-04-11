"""Cross-replica notification via Redis Pub/Sub (or local-only fallback).

When multiple relay replicas run behind a load balancer, long-poll wakeups
and SSE pushes are process-local.  This service bridges the gap by publishing
notifications to Redis Pub/Sub so that *all* replicas observe the event.

If no Redis connection is provided the service falls back to local-only
dispatch, preserving existing single-process behavior for tests and dev.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, Callable

import structlog

logger = structlog.get_logger("murmur.services.notification")

# Type alias for notification handlers
Handler = Callable[[dict], Any]


class NotificationService:
    """Pub/Sub notification bus — Redis-backed or local-only."""

    def __init__(self, redis_conn: Any | None = None) -> None:
        self._redis = redis_conn
        # channel -> list of local handler callables
        self._local_handlers: dict[str, list[Handler]] = defaultdict(list)
        self._listener_task: asyncio.Task | None = None
        self._pubsub: Any | None = None
        self._stopped = False

    # -- Channel helpers ---------------------------------------------------

    @staticmethod
    def dm_channel(tenant_id: str, recipient: str) -> str:
        return f"t:{tenant_id}:notify:dm:{recipient}"

    @staticmethod
    def room_channel(tenant_id: str, room_id: str) -> str:
        return f"t:{tenant_id}:notify:room:{room_id}"

    # -- Publish -----------------------------------------------------------

    async def publish(self, channel: str, message: dict) -> None:
        """Publish *message* to *channel*.

        If Redis is available the message is published there (the background
        listener dispatches to local handlers).  Otherwise we dispatch
        directly to local handlers.
        """
        if self._redis:
            try:
                await self._redis.publish(channel, json.dumps(message))
            except Exception:
                logger.warning("Redis publish failed, falling back to local")
                self._dispatch_local(channel, message)
        else:
            self._dispatch_local(channel, message)

    # -- Subscribe / unsubscribe -------------------------------------------

    def subscribe(self, channel: str, handler: Handler) -> None:
        """Register a local handler for *channel*."""
        self._local_handlers[channel].append(handler)

    def unsubscribe(self, channel: str, handler: Handler) -> None:
        """Remove a previously registered handler."""
        handlers = self._local_handlers.get(channel, [])
        if handler in handlers:
            handlers.remove(handler)
        if not handlers:
            self._local_handlers.pop(channel, None)

    # -- Lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the background Redis Pub/Sub listener (if Redis available)."""
        if not self._redis:
            return
        self._stopped = False
        self._pubsub = self._redis.pubsub()
        # Subscribe to the wildcard notify pattern
        await self._pubsub.psubscribe("t:*:notify:*")
        self._listener_task = asyncio.create_task(self._listen())
        logger.info("NotificationService started (Redis Pub/Sub)")

    async def stop(self) -> None:
        """Shut down the background listener cleanly."""
        self._stopped = True
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            try:
                await self._pubsub.punsubscribe("t:*:notify:*")
                await self._pubsub.aclose()
            except Exception:
                pass
            self._pubsub = None
        self._listener_task = None
        logger.info("NotificationService stopped")

    # -- Internal ----------------------------------------------------------

    def _dispatch_local(self, channel: str, message: dict) -> None:
        for handler in list(self._local_handlers.get(channel, [])):
            try:
                handler(message)
            except Exception:
                logger.exception("Handler error on channel %s", channel)

    async def _listen(self) -> None:
        """Background coroutine that reads Redis Pub/Sub messages."""
        try:
            while not self._stopped:
                msg = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg is None:
                    continue
                if msg["type"] not in ("pmessage",):
                    continue
                channel = msg.get("channel", "")
                try:
                    data = json.loads(msg["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                self._dispatch_local(channel, data)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Notification listener crashed")
