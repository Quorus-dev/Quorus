"""Direct-message service — send, fetch (with long-poll), and peek.

Long-poll wakeup is signaled via :class:`NotificationService` for cross-replica
delivery.  When no NotificationService is configured (tests / single-process),
falls back to process-local ``asyncio.Event``.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog
from fastapi import HTTPException

from murmur.backends.protocol import MessageBackend
from murmur.routes.helpers import _chunk_content, _reassemble_chunks
from murmur.services.analytics_svc import AnalyticsService
from murmur.services.rate_limit_svc import RateLimitService
from murmur.services.sse_svc import SSEService
from murmur.services.webhook_svc import WebhookService


@dataclass
class FetchResult:
    """Per-request fetch result with at-least-once delivery.

    Two ACK modes:

    1. **Server-side ACK** (default, backward compat): caller calls
       ``await result.ack()`` after building the response.
    2. **Client-side ACK**: caller returns messages with their IDs,
       client calls ``POST /messages/{recipient}/ack`` with those IDs.
       Unacked messages are redelivered after the visibility timeout.

    Each FetchResult is independent — no shared mutable state.
    """

    messages: list[dict]
    _backend: MessageBackend = field(repr=False)
    _tenant_id: str = field(repr=False)
    _recipient: str = field(repr=False)
    ack_token: str = ""

    async def ack(self) -> None:
        """Server-side ACK: permanently remove fetched messages."""
        if self.ack_token:
            await self._backend.ack(
                self._tenant_id, self._recipient, self.ack_token
            )

if TYPE_CHECKING:
    from murmur.services.notification_svc import NotificationService

logger = structlog.get_logger("murmur.services.message")

MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "1000"))


class MessageService:
    """Encapsulates DM send/fetch/peek business logic."""

    def __init__(
        self,
        backend: MessageBackend,
        sse: SSEService,
        webhook: WebhookService,
        analytics: AnalyticsService,
        rate_limit: RateLimitService,
        max_message_size: int = MAX_MESSAGE_SIZE,
        notification: NotificationService | None = None,
    ) -> None:
        self._backend = backend
        self._sse = sse
        self._webhook = webhook
        self._analytics = analytics
        self._rate_limit = rate_limit
        self._max_message_size = max_message_size
        self._notification = notification
        # Long-poll wakeup events keyed by (tenant_id, recipient)
        self._events: dict[tuple[str, str], asyncio.Event] = defaultdict(
            asyncio.Event
        )

    def _event_key(
        self, tenant_id: str, recipient: str
    ) -> tuple[str, str]:
        return (tenant_id, recipient)

    def notify_new_message(self, tenant_id: str, recipient: str) -> None:
        """Signal that a new message is available for long-poll wakeup.

        Sets the local asyncio.Event and, if available, publishes via
        NotificationService so other replicas wake their long-pollers.
        """
        self._events[self._event_key(tenant_id, recipient)].set()
        if self._notification:
            from murmur.services.notification_svc import NotificationService as NS

            channel = NS.dm_channel(tenant_id, recipient)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._notification.publish(channel, {"wake": True})
                )
            except RuntimeError:
                pass

    def _subscribe_wakeup(self, tenant_id: str, recipient: str) -> None:
        """Subscribe to cross-replica notifications for long-poll wakeup."""
        if not self._notification:
            return
        from murmur.services.notification_svc import NotificationService as NS

        channel = NS.dm_channel(tenant_id, recipient)
        ekey = self._event_key(tenant_id, recipient)

        def _on_notify(msg: dict) -> None:
            self._events[ekey].set()

        # Store on the event so we can unsubscribe
        event = self._events[ekey]
        if not hasattr(event, "_notify_handler"):
            event._notify_handler = _on_notify  # type: ignore[attr-defined]
            event._notify_channel = channel  # type: ignore[attr-defined]
            self._notification.subscribe(channel, _on_notify)

    def _unsubscribe_wakeup(self, tenant_id: str, recipient: str) -> None:
        """Unsubscribe cross-replica notification handler."""
        if not self._notification:
            return
        ekey = self._event_key(tenant_id, recipient)
        event = self._events.get(ekey)
        if event is None:
            return
        handler = getattr(event, "_notify_handler", None)
        channel = getattr(event, "_notify_channel", None)
        if handler and channel:
            self._notification.unsubscribe(channel, handler)
            del event._notify_handler  # type: ignore[attr-defined]
            del event._notify_channel  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send_dm(
        self,
        tenant_id: str,
        sender: str,
        recipient: str,
        content: str,
    ) -> dict:
        """Enqueue a DM, handling chunking, rate-limiting, SSE push, and webhooks.

        Returns ``{"id": ..., "timestamp": ...}``.
        """
        # Rate limit
        if not await self._rate_limit.check(tenant_id, sender):
            raise HTTPException(
                status_code=429, detail="Rate limit exceeded"
            )

        timestamp = datetime.now(timezone.utc).isoformat()
        ekey = self._event_key(tenant_id, recipient)

        if len(content.encode("utf-8")) > self._max_message_size:
            return await self._send_chunked(
                tenant_id, sender, recipient, content, timestamp, ekey
            )

        message = {
            "id": str(uuid.uuid4()),
            "from_name": sender,
            "to": recipient,
            "room": None,
            "content": content,
            "timestamp": timestamp,
        }
        await self._backend.enqueue(tenant_id, recipient, message)
        self.notify_new_message(tenant_id, recipient)
        self._sse.push(tenant_id, recipient, message)
        await self._webhook.notify_dm(tenant_id, recipient, message)
        await self._analytics.track_send(tenant_id, sender)

        logger.info("Message %s: %s -> %s", message["id"], sender, recipient)
        return {"id": message["id"], "timestamp": timestamp}

    async def _send_chunked(
        self,
        tenant_id: str,
        sender: str,
        recipient: str,
        content: str,
        timestamp: str,
        ekey: tuple[str, str],
    ) -> dict:
        chunk_group = str(uuid.uuid4())
        chunks = _chunk_content(content, self._max_message_size)
        chunk_total = len(chunks)

        if chunk_total > MAX_MESSAGES:
            raise HTTPException(
                status_code=413,
                detail="Message is too large for the configured storage limit",
            )

        messages = [
            {
                "id": str(uuid.uuid4()),
                "from_name": sender,
                "to": recipient,
                "room": None,
                "content": chunk_content,
                "timestamp": timestamp,
                "chunk_group": chunk_group,
                "chunk_index": idx,
                "chunk_total": chunk_total,
            }
            for idx, chunk_content in enumerate(chunks)
        ]

        await self._backend.enqueue_batch(tenant_id, recipient, messages)
        self.notify_new_message(tenant_id, recipient)

        # Push the reassembled message over SSE / webhook
        reassembled = {
            "id": messages[0]["id"],
            "from_name": sender,
            "to": recipient,
            "content": content,
            "timestamp": timestamp,
        }
        self._sse.push(tenant_id, recipient, reassembled)
        await self._webhook.notify_dm(tenant_id, recipient, reassembled)
        await self._analytics.track_send(tenant_id, sender)

        logger.info(
            "Message chunked into %d parts (%s -> %s, group %s)",
            chunk_total,
            sender,
            recipient,
            chunk_group,
        )
        return {"id": messages[0]["id"], "timestamp": timestamp}

    # ------------------------------------------------------------------
    # Fetch (with long-poll support)
    # ------------------------------------------------------------------

    async def fetch(
        self,
        tenant_id: str,
        recipient: str,
        wait: int = 0,
    ) -> "FetchResult":
        """Dequeue and reassemble messages for *recipient*.

        Returns a FetchResult with messages and a per-request ack() method.
        Caller MUST call result.ack() after the response is sent.
        """
        wait = min(max(wait, 0), 60)
        ekey = self._event_key(tenant_id, recipient)

        ready, held_back, ack_token = await self._fetch_and_reassemble(
            tenant_id, recipient, ekey
        )

        if not ready and wait > 0:
            self._subscribe_wakeup(tenant_id, recipient)
            try:
                await asyncio.wait_for(
                    self._events[ekey].wait(), timeout=wait
                )
            except asyncio.TimeoutError:
                pass
            finally:
                self._unsubscribe_wakeup(tenant_id, recipient)
            ready, held_back, ack_token = await self._fetch_and_reassemble(
                tenant_id, recipient, ekey
            )

        if ready:
            await self._analytics.track_delivery(
                tenant_id, recipient, len(ready)
            )

        logger.info(
            "Fetched %d messages for %s (%d chunks held back)",
            len(ready),
            recipient,
            len(held_back),
        )
        return FetchResult(
            messages=ready,
            ack_token=ack_token,
            _backend=self._backend,
            _tenant_id=tenant_id,
            _recipient=recipient,
        )

    async def _fetch_and_reassemble(
        self,
        tenant_id: str,
        recipient: str,
        ekey: tuple[str, str],
    ) -> tuple[list[dict], list[dict], str]:
        """Fetch messages, reassemble chunks, re-enqueue incomplete groups.

        Returns (ready, held_back, ack_token). Caller must ACK after response.
        """
        all_msgs, ack_token = await self._backend.fetch(tenant_id, recipient)
        self._events[ekey].clear()
        ready, held_back = _reassemble_chunks(all_msgs)

        # Re-enqueue incomplete chunk groups so they survive
        if held_back:
            await self._backend.enqueue_batch(
                tenant_id, recipient, held_back
            )
        return ready, held_back, ack_token

    # ------------------------------------------------------------------
    # Client ACK
    # ------------------------------------------------------------------

    async def ack_by_token(
        self, tenant_id: str, recipient: str, ack_token: str
    ) -> None:
        """Acknowledge all messages from a previous fetch (by token)."""
        await self._backend.ack(tenant_id, recipient, ack_token)

    async def ack_by_ids(
        self, tenant_id: str, recipient: str, message_ids: list[str]
    ) -> int:
        """Acknowledge specific message IDs. Returns count of newly acked."""
        return await self._backend.ack_ids(tenant_id, recipient, message_ids)

    # ------------------------------------------------------------------
    # Peek
    # ------------------------------------------------------------------

    async def peek(self, tenant_id: str, recipient: str) -> int:
        """Return the number of pending messages without consuming them."""
        return await self._backend.peek(tenant_id, recipient)

    async def pending(self, tenant_id: str, recipient: str) -> int:
        """Return the number of delivered-but-unacked messages."""
        return await self._backend.pending_count(tenant_id, recipient)
