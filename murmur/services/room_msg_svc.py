"""Room messaging service — send (with fan-out), history, and search."""

from __future__ import annotations

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

        room_name = room_data.get("name", "")
        timestamp = datetime.now(timezone.utc).isoformat()
        message_id = str(uuid.uuid4())

        # Fan-out to each member's DM queue
        for recipient in members:
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
            await self._msg_backend.enqueue(tenant_id, recipient, fan_out_msg)
            if self._on_enqueue:
                self._on_enqueue(tenant_id, recipient)
            self._sse.push(tenant_id, recipient, fan_out_msg)

        # Append to room history
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
        await self._history.append(tenant_id, room_id, history_msg)

        # Analytics and webhook notification
        await self._analytics.track_send(tenant_id, sender)
        await self._webhook.notify_room(tenant_id, room_id, history_msg)

        logger.info(
            "Room message %s in %s: %s -> %d recipients",
            message_id,
            room_name,
            sender,
            len(members),
        )
        return {"id": message_id, "timestamp": timestamp}

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
