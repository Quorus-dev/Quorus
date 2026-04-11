"""Direct-message service — send, fetch (with long-poll), and peek.

Owns the asyncio.Event dict for long-poll wakeup signaling.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import structlog
from fastapi import HTTPException

from murmur.backends.protocol import MessageBackend
from murmur.routes.helpers import _chunk_content, _reassemble_chunks
from murmur.services.analytics_svc import AnalyticsService
from murmur.services.rate_limit_svc import RateLimitService
from murmur.services.sse_svc import SSEService
from murmur.services.webhook_svc import WebhookService

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
    ) -> None:
        self._backend = backend
        self._sse = sse
        self._webhook = webhook
        self._analytics = analytics
        self._rate_limit = rate_limit
        self._max_message_size = max_message_size
        # Long-poll wakeup events keyed by (tenant_id, recipient)
        self._events: dict[tuple[str, str], asyncio.Event] = defaultdict(
            asyncio.Event
        )

    def _event_key(
        self, tenant_id: str, recipient: str
    ) -> tuple[str, str]:
        return (tenant_id, recipient)

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
        self._events[ekey].set()
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
        self._events[ekey].set()

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
    ) -> list[dict]:
        """Dequeue and reassemble messages for *recipient*.

        If *wait* > 0 and no messages are ready, long-poll up to *wait* seconds.
        """
        wait = min(max(wait, 0), 60)
        ekey = self._event_key(tenant_id, recipient)

        ready, held_back = await self._dequeue_and_reassemble(
            tenant_id, recipient, ekey
        )

        if not ready and wait > 0:
            try:
                await asyncio.wait_for(
                    self._events[ekey].wait(), timeout=wait
                )
            except asyncio.TimeoutError:
                pass
            ready, held_back = await self._dequeue_and_reassemble(
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
        return ready

    async def _dequeue_and_reassemble(
        self,
        tenant_id: str,
        recipient: str,
        ekey: tuple[str, str],
    ) -> tuple[list[dict], list[dict]]:
        """Dequeue all, reassemble chunks, re-enqueue incomplete groups."""
        all_msgs = await self._backend.dequeue_all(tenant_id, recipient)
        self._events[ekey].clear()
        ready, held_back = _reassemble_chunks(all_msgs)
        if held_back:
            await self._backend.enqueue_batch(
                tenant_id, recipient, held_back
            )
        return ready, held_back

    # ------------------------------------------------------------------
    # Peek
    # ------------------------------------------------------------------

    async def peek(self, tenant_id: str, recipient: str) -> int:
        """Return the number of pending messages without consuming them."""
        return await self._backend.peek(tenant_id, recipient)
