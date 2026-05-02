"""Audit ledger model for message lifecycle tracking."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from quorus.storage.base import Base


class AuditEvent(str, Enum):
    """Audit event types for message lifecycle."""

    # Message creation
    MESSAGE_CREATED = "message_created"
    MESSAGE_QUEUED = "message_queued"  # Added to outbox

    # Fan-out processing
    FANOUT_STARTED = "fanout_started"
    FANOUT_COMPLETED = "fanout_completed"
    FANOUT_PARTIAL = "fanout_partial"  # Some recipients failed
    FANOUT_FAILED = "fanout_failed"  # All recipients failed
    FANOUT_RETRY = "fanout_retry"

    # Per-recipient delivery
    DELIVERED = "delivered"  # Enqueued to recipient's inbox
    DELIVERY_REJECTED = "delivery_rejected"  # Queue full

    # Webhook events
    WEBHOOK_SENT = "webhook_sent"
    WEBHOOK_FAILED = "webhook_failed"

    # SSE events
    SSE_PUSHED = "sse_pushed"

    # DM events (non-room messages)
    DM_SENT = "dm_sent"
    DM_DELIVERED = "dm_delivered"
    DM_READ = "dm_read"  # ACK'd by recipient

    # MCP tool execution
    MCP_TOOL_CALLED = "mcp_tool_called"

    # Policy engine decisions
    POLICY_EVALUATED = "policy_evaluated"


class AuditLedger(Base):
    """Audit ledger entry for message tracing.

    Records every significant event in a message's lifecycle,
    enabling "what happened to message X?" queries.
    """

    __tablename__ = "audit_ledger"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    room_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    room_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    receipt_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    def to_dict(self) -> dict:
        """Convert to API response dict."""
        return {
            "id": str(self.id),
            "tenant_id": self.tenant_id,
            "message_id": str(self.message_id),
            "room_id": self.room_id,
            "room_name": self.room_name,
            "event_type": self.event_type,
            "actor": self.actor,
            "target": self.target,
            "details": self.details,
            "error": self.error,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            "receipt_signature": self.receipt_signature,
            "created_at": self.created_at.isoformat(),
        }
