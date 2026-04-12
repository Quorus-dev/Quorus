"""Outbox service — background worker for transactional message fan-out.

The outbox pattern ensures at-least-once delivery:
1. Room send writes to history + outbox in same Postgres transaction
2. This worker polls for pending entries
3. Worker claims entries (atomic status update)
4. Worker performs fan-out to Redis + SSE + webhooks
5. Worker marks completed or retries with backoff

If the worker crashes mid-processing:
- claimed_at timestamp allows stale claim detection
- Entries stuck in "processing" too long get reclaimed
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from murmur.backends.protocol import MessageBackend
from murmur.models.outbox import MessageOutbox, OutboxStatus
from murmur.services.room_svc import RoomService
from murmur.services.sse_svc import SSEService
from murmur.services.webhook_svc import WebhookService
from murmur.storage.postgres import get_db_session

logger = structlog.get_logger("murmur.services.outbox")

# Configuration
BATCH_SIZE = int(os.environ.get("OUTBOX_BATCH_SIZE", "50"))
POLL_INTERVAL = float(os.environ.get("OUTBOX_POLL_INTERVAL", "1.0"))
MAX_RETRIES = int(os.environ.get("OUTBOX_MAX_RETRIES", "5"))
CLAIM_TIMEOUT = int(os.environ.get("OUTBOX_CLAIM_TIMEOUT", "60"))
MAX_RECIPIENT_DEPTH = int(os.environ.get("MAX_RECIPIENT_DEPTH", "10000"))

# Exponential backoff: 1s, 2s, 4s, 8s, 16s
RETRY_DELAYS = [1, 2, 4, 8, 16]


class OutboxWorker:
    """Background worker that processes the message outbox."""

    def __init__(
        self,
        room_svc: RoomService,
        msg_backend: MessageBackend,
        sse_svc: SSEService,
        webhook_svc: WebhookService,
        on_enqueue: Callable[[str, str], None] | None = None,
    ) -> None:
        self._room = room_svc
        self._msg_backend = msg_backend
        self._sse = sse_svc
        self._webhook = webhook_svc
        self._on_enqueue = on_enqueue
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._running = False

    async def start(self) -> None:
        """Start the background worker loop."""
        self._running = True
        logger.info("Outbox worker started", worker_id=self._worker_id)

        while self._running:
            try:
                processed = await self._process_batch()
                if processed == 0:
                    # No work — back off
                    await asyncio.sleep(POLL_INTERVAL)
            except Exception as e:
                logger.error("Outbox worker error", error=str(e))
                await asyncio.sleep(POLL_INTERVAL * 2)

    def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False
        logger.info("Outbox worker stopping", worker_id=self._worker_id)

    async def _process_batch(self) -> int:
        """Claim and process a batch of pending entries. Returns count processed."""
        async with get_db_session() as session:
            # 1. Reclaim stale entries (stuck in processing too long)
            await self._reclaim_stale(session)

            # 2. Claim pending entries
            entries = await self._claim_entries(session)
            if not entries:
                return 0

            # 3. Process each entry
            processed = 0
            for entry in entries:
                try:
                    await self._process_entry(session, entry)
                    processed += 1
                except Exception as e:
                    logger.error(
                        "Failed to process outbox entry",
                        entry_id=str(entry.id),
                        error=str(e),
                    )

            return processed

    async def _reclaim_stale(self, session: AsyncSession) -> None:
        """Reclaim entries stuck in processing state too long."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=CLAIM_TIMEOUT)

        result = await session.execute(
            update(MessageOutbox)
            .where(
                MessageOutbox.status == OutboxStatus.PROCESSING.value,
                MessageOutbox.claimed_at < cutoff,
            )
            .values(
                status=OutboxStatus.PENDING.value,
                claimed_at=None,
                claimed_by=None,
            )
            .returning(MessageOutbox.id)
        )
        reclaimed = result.fetchall()
        if reclaimed:
            logger.warning(
                "Reclaimed stale outbox entries",
                count=len(reclaimed),
                worker_id=self._worker_id,
            )

    async def _claim_entries(self, session: AsyncSession) -> list[MessageOutbox]:
        """Atomically claim a batch of pending entries."""
        now = datetime.now(timezone.utc)

        # Find entries that are pending OR due for retry
        result = await session.execute(
            select(MessageOutbox)
            .where(MessageOutbox.status == OutboxStatus.PENDING.value)
            .order_by(MessageOutbox.created_at)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        entries = list(result.scalars().all())

        if not entries:
            return []

        # Update status to processing
        entry_ids = [e.id for e in entries]
        await session.execute(
            update(MessageOutbox)
            .where(MessageOutbox.id.in_(entry_ids))
            .values(
                status=OutboxStatus.PROCESSING.value,
                claimed_at=now,
                claimed_by=self._worker_id,
            )
        )

        logger.debug(
            "Claimed outbox entries",
            count=len(entries),
            worker_id=self._worker_id,
        )
        return entries

    async def _process_entry(
        self, session: AsyncSession, entry: MessageOutbox
    ) -> None:
        """Process a single outbox entry — fan-out to all room members."""
        try:
            # Get room members
            room_id, room_data = await self._room.get(entry.tenant_id, entry.room_id)
            members = room_data.get("members", {})

            if not members:
                logger.warning(
                    "Outbox entry for room with no members",
                    entry_id=str(entry.id),
                    room=entry.room_name,
                )
                await self._mark_completed(session, entry)
                return

            # Build fan-out messages
            timestamp = entry.created_at.isoformat()
            fanout_messages: dict[str, dict] = {}
            for recipient in members:
                fanout_messages[recipient] = entry.to_fanout_message(recipient, timestamp)

            # Batch enqueue to Redis
            rejected = await self._msg_backend.enqueue_fanout(
                entry.tenant_id, fanout_messages, maxlen=MAX_RECIPIENT_DEPTH
            )

            # Push to SSE and notify callbacks
            for recipient, msg in fanout_messages.items():
                if recipient in rejected:
                    continue
                if self._on_enqueue:
                    self._on_enqueue(entry.tenant_id, recipient)
                self._sse.push(entry.tenant_id, recipient, msg)

            # Send webhook notification
            history_msg = {
                "id": str(entry.message_id),
                "from": entry.sender,
                "from_name": entry.sender,
                "room": entry.room_name,
                "content": entry.content,
                "message_type": entry.message_type,
                "timestamp": timestamp,
                "reply_to": str(entry.reply_to) if entry.reply_to else None,
            }
            await self._webhook.notify_room(entry.tenant_id, room_id, history_msg)

            # Mark completed
            await self._mark_completed(session, entry)

            delivered = len(members) - len(rejected)
            logger.info(
                "Outbox entry processed",
                entry_id=str(entry.id),
                message_id=str(entry.message_id),
                room=entry.room_name,
                delivered=delivered,
                rejected=len(rejected),
            )

        except Exception as e:
            await self._handle_failure(session, entry, str(e))

    async def _mark_completed(
        self, session: AsyncSession, entry: MessageOutbox
    ) -> None:
        """Mark an entry as successfully processed."""
        await session.execute(
            update(MessageOutbox)
            .where(MessageOutbox.id == entry.id)
            .values(
                status=OutboxStatus.COMPLETED.value,
                processed_at=datetime.now(timezone.utc),
            )
        )

    async def _handle_failure(
        self, session: AsyncSession, entry: MessageOutbox, error: str
    ) -> None:
        """Handle a processing failure — retry or move to DLQ."""
        new_retry_count = entry.retry_count + 1

        if new_retry_count >= MAX_RETRIES:
            # Move to DLQ (failed state)
            await session.execute(
                update(MessageOutbox)
                .where(MessageOutbox.id == entry.id)
                .values(
                    status=OutboxStatus.FAILED.value,
                    retry_count=new_retry_count,
                    last_error=error,
                    processed_at=datetime.now(timezone.utc),
                )
            )
            logger.error(
                "Outbox entry moved to DLQ after max retries",
                entry_id=str(entry.id),
                message_id=str(entry.message_id),
                retries=new_retry_count,
                error=error,
            )
        else:
            # Reset to pending for retry
            await session.execute(
                update(MessageOutbox)
                .where(MessageOutbox.id == entry.id)
                .values(
                    status=OutboxStatus.PENDING.value,
                    retry_count=new_retry_count,
                    last_error=error,
                    claimed_at=None,
                    claimed_by=None,
                )
            )
            delay = RETRY_DELAYS[min(new_retry_count - 1, len(RETRY_DELAYS) - 1)]
            logger.warning(
                "Outbox entry scheduled for retry",
                entry_id=str(entry.id),
                retry=new_retry_count,
                delay=delay,
                error=error,
            )


async def cleanup_completed(max_age_hours: int = 24) -> int:
    """Delete completed outbox entries older than max_age_hours.

    Run this periodically (e.g., daily) to prevent table bloat.
    Returns count of deleted entries.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    async with get_db_session() as session:
        from sqlalchemy import delete

        result = await session.execute(
            delete(MessageOutbox).where(
                MessageOutbox.status == OutboxStatus.COMPLETED.value,
                MessageOutbox.processed_at < cutoff,
            )
        )
        deleted = result.rowcount
        if deleted:
            logger.info("Cleaned up completed outbox entries", count=deleted)
        return deleted
