"""Message outbox model for transactional fan-out."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from murmur.storage.base import Base


class OutboxStatus(str, Enum):
    """Outbox entry status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageOutbox(Base):
    """Outbox entry for transactional message fan-out.

    The outbox pattern:
    1. Room send writes to message_history + message_outbox atomically
    2. Background worker claims entries (status=processing)
    3. Worker performs fan-out to Redis queues + SSE + webhooks
    4. Worker marks entry completed or increments retry_count
    5. After max retries, entry moves to failed (DLQ)
    """

    __tablename__ = "message_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    room_id: Mapped[str] = mapped_column(String(64), nullable=False)
    room_name: Mapped[str] = mapped_column(String(256), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sender: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="chat")
    reply_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OutboxStatus.PENDING.value, index=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_fanout_message(self, recipient: str, timestamp: str) -> dict:
        """Build a fan-out message dict for a recipient."""
        return {
            "id": str(uuid.uuid4()),
            "from_name": self.sender,
            "to": recipient,
            "room": self.room_name,
            "content": self.content,
            "message_type": self.message_type,
            "timestamp": timestamp,
            "reply_to": str(self.reply_to) if self.reply_to else None,
        }
