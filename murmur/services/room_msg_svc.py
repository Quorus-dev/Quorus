"""Room messaging service — send (with fan-out), history, and search."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Callable

import structlog
from fastapi import HTTPException

from murmur.backends.protocol import MessageBackend, RoomHistoryBackend
from murmur.services.analytics_svc import AnalyticsService
from murmur.services.rate_limit_svc import RateLimitService
from murmur.services.room_svc import RoomService
from murmur.services.sse_svc import SSEService
from murmur.services.webhook_svc import WebhookService

logger = structlog.get_logger("murmur.services.room_msg")

MAX_RECIPIENT_DEPTH = int(os.environ.get("MAX_RECIPIENT_DEPTH", "10000"))


class RoomMessageService:
    """Handles room message fan-out, history storage, and search."""

    def __init__(
        self,
        room: RoomService,
        history: RoomHistoryBackend,
        msg_backend: MessageBackend,
        sse: SSEService,
        webhook: WebhookService,
        analytics: AnalyticsService,
        rate_limit: RateLimitService,
        on_enqueue: Callable[[str, str], None] | None = None,
    ) -> None:
        self._room = room
        self._history = history
        self._msg_backend = msg_backend
        self._sse = sse
        self._webhook = webhook
        self._analytics = analytics
        self._rate_limit = rate_limit
        # Callback to signal long-poll wakeup: on_enqueue(tenant_id, recipient)
        self._on_enqueue = on_enqueue

    # ------------------------------------------------------------------
    # Send (fan-out to member DM queues)
    # ------------------------------------------------------------------

    async def get_thread(
        self, tenant_id: str, room_id: str, message_id: str
    ) -> list[dict]:
        """Return the parent message and all its replies."""
        room_id, _ = await self._room.get(tenant_id, room_id)
        return await self._history.get_thread(tenant_id, room_id, message_id)

    async def send(
        self,
        tenant_id: str,
        room_id: str,
        sender: str,
        content: str,
        message_type: str = "chat",
        reply_to: str | None = None,
    ) -> dict:
        """Send a message to a room, fan-out to all members.

        Uses direct backend.enqueue + SSE push (not MessageService.send_dm)
        to avoid per-fan-out rate limiting.

        Returns ``{"id": ..., "timestamp": ...}``.
        """
        # Rate limit the sender
        if not await self._rate_limit.check(tenant_id, sender):
            raise HTTPException(
                status_code=429, detail="Rate limit exceeded"
            )

        # Resolve room and verify membership
        room_id, room_data = await self._room.get(tenant_id, room_id)
        members = room_data.get("members", {})
        if sender not in members:
            raise HTTPException(
                status_code=403, detail="Not a member of this room"
            )

        # Validate reply_to refers to a real message in this room
        if reply_to is not None:
            parent = await self._history.get_by_id(tenant_id, room_id, reply_to)
            if parent is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"reply_to message '{reply_to}' not found in this room",
                )

        room_name = room_data.get("name", "")
        timestamp = datetime.now(timezone.utc).isoformat()
        message_id = str(uuid.uuid4())

        # Build history message (canonical record)
        history_msg = {
            "id": message_id,
            "from": sender,
            "from_name": sender,
            "room": room_name,
            "content": content,
            "message_type": message_type,
            "timestamp": timestamp,
            "reply_to": reply_to,
        }

        # STEP 1: Persist to history FIRST (Postgres is source of truth)
        # If this fails, we haven't touched Redis — clean rollback via idempotency
        await self._history.append(tenant_id, room_id, history_msg)

        # STEP 2: Fan-out to each member's DM queue (with backpressure)
        # If this fails after Postgres commit, message is in history but
        # recipients may miss real-time delivery — they can poll history.
        # This is better than the reverse (recipients get message, history fails,
        # retry duplicates fan-out).
        skipped_recipients: list[str] = []
        fanout_messages: dict[str, dict] = {}

        for recipient in members:
            # Check quota before enqueueing
            depth = await self._msg_backend.recipient_depth(tenant_id, recipient)
            if depth >= MAX_RECIPIENT_DEPTH:
                skipped_recipients.append(recipient)
                logger.warning(
                    "Room fan-out skipped: recipient queue full",
                    room=room_name,
                    recipient=recipient,
                    depth=depth,
                )
                continue

            fan_out_msg = {
                "id": str(uuid.uuid4()),
                "from_name": sender,
                "to": recipient,
                "room": room_name,
                "content": content,
                "message_type": message_type,
                "timestamp": timestamp,
                "reply_to": reply_to,
            }
            fanout_messages[recipient] = fan_out_msg

        # Batch enqueue all messages in a single operation (pipelined for Redis)
        # Pass maxlen to enforce atomic backpressure — even if concurrent sends
        # all pass the advisory depth check, MAXLEN prevents overshooting
        if fanout_messages:
            await self._msg_backend.enqueue_fanout(
                tenant_id, fanout_messages, maxlen=MAX_RECIPIENT_DEPTH
            )

            # Notify via SSE and callbacks
            for recipient, fan_out_msg in fanout_messages.items():
                if self._on_enqueue:
                    self._on_enqueue(tenant_id, recipient)
                self._sse.push(tenant_id, recipient, fan_out_msg)

        # Analytics and webhook notification
        await self._analytics.track_send(tenant_id, sender)
        await self._webhook.notify_room(tenant_id, room_id, history_msg)

        delivered_count = len(members) - len(skipped_recipients)
        logger.info(
            "Room message %s in %s: %s -> %d/%d recipients",
            message_id,
            room_name,
            sender,
            delivered_count,
            len(members),
        )
        result = {"id": message_id, "timestamp": timestamp}
        if skipped_recipients:
            result["skipped_recipients"] = skipped_recipients
            result["warning"] = (
                f"{len(skipped_recipients)} recipient(s) skipped due to full queues"
            )
        return result

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def history(
        self, tenant_id: str, room_id: str, limit: int = 50
    ) -> list[dict]:
        """Return the most recent *limit* messages from room history."""
        room_id, _ = await self._room.get(tenant_id, room_id)
        limit = min(max(limit, 1), 200)
        return await self._history.get_recent(tenant_id, room_id, limit)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        tenant_id: str,
        room_id: str,
        q: str = "",
        sender: str = "",
        message_type: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """Search room history with optional filters."""
        room_id, _ = await self._room.get(tenant_id, room_id)
        limit = min(max(limit, 1), 200)
        return await self._history.search(
            tenant_id, room_id, q=q, sender=sender,
            message_type=message_type, limit=limit,
        )
