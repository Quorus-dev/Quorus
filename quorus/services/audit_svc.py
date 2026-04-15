"""Audit service — records message lifecycle events for tracing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import delete, select

from quorus.models.audit import AuditEvent, AuditLedger
from quorus.storage.postgres import get_db_session

logger = structlog.get_logger("quorus.services.audit")


class AuditService:
    """Records and queries message lifecycle events.

    Usage:
        audit = AuditService()

        # Record an event
        await audit.record(
            tenant_id="t-123",
            message_id=uuid.UUID("..."),
            event_type=AuditEvent.MESSAGE_CREATED,
            actor="alice",
            room_id="room-1",
            room_name="general",
        )

        # Query message timeline
        events = await audit.get_message_timeline("t-123", message_id)
    """

    async def record(
        self,
        tenant_id: str,
        message_id: uuid.UUID,
        event_type: AuditEvent | str,
        actor: str | None = None,
        target: str | None = None,
        room_id: str | None = None,
        room_name: str | None = None,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Record an audit event.

        This is fire-and-forget — failures are logged but don't block.
        """
        try:
            async with get_db_session() as session:
                event = AuditLedger(
                    tenant_id=tenant_id,
                    message_id=message_id,
                    event_type=event_type.value
                    if isinstance(event_type, AuditEvent)
                    else event_type,
                    actor=actor,
                    target=target,
                    room_id=room_id,
                    room_name=room_name,
                    details=details,
                    error=error,
                )
                session.add(event)

            logger.debug(
                "Audit event recorded",
                message_id=str(message_id),
                event_type=event_type,
                actor=actor,
            )
        except Exception as e:
            # Audit failures are non-fatal — log and continue
            logger.warning(
                "Failed to record audit event",
                message_id=str(message_id),
                event_type=event_type,
                error=str(e),
            )

    async def record_batch(
        self,
        tenant_id: str,
        message_id: uuid.UUID,
        events: list[dict],
    ) -> None:
        """Record multiple audit events for a message.

        Each event dict should have: event_type, and optionally:
        actor, target, room_id, room_name, details, error
        """
        try:
            async with get_db_session() as session:
                for event_data in events:
                    event = AuditLedger(
                        tenant_id=tenant_id,
                        message_id=message_id,
                        event_type=event_data.get("event_type"),
                        actor=event_data.get("actor"),
                        target=event_data.get("target"),
                        room_id=event_data.get("room_id"),
                        room_name=event_data.get("room_name"),
                        details=event_data.get("details"),
                        error=event_data.get("error"),
                    )
                    session.add(event)

            logger.debug(
                "Audit events recorded",
                message_id=str(message_id),
                count=len(events),
            )
        except Exception as e:
            logger.warning(
                "Failed to record audit events",
                message_id=str(message_id),
                error=str(e),
            )

    async def get_message_timeline(
        self,
        tenant_id: str,
        message_id: uuid.UUID,
    ) -> list[dict]:
        """Get the full audit timeline for a message.

        Returns events in chronological order.
        """
        async with get_db_session() as session:
            result = await session.execute(
                select(AuditLedger)
                .where(
                    AuditLedger.tenant_id == tenant_id,
                    AuditLedger.message_id == message_id,
                )
                .order_by(AuditLedger.created_at)
            )
            return [e.to_dict() for e in result.scalars().all()]

    async def get_recent_events(
        self,
        tenant_id: str,
        event_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get recent audit events for a tenant.

        Useful for debugging and monitoring dashboards.
        """
        query = select(AuditLedger).where(AuditLedger.tenant_id == tenant_id)

        if event_types:
            query = query.where(AuditLedger.event_type.in_(event_types))

        query = query.order_by(AuditLedger.created_at.desc()).limit(limit)

        async with get_db_session() as session:
            result = await session.execute(query)
            return [e.to_dict() for e in result.scalars().all()]

    async def get_failed_deliveries(
        self,
        tenant_id: str,
        since_hours: int = 24,
        limit: int = 100,
    ) -> list[dict]:
        """Get recent failed delivery events for investigation."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

        async with get_db_session() as session:
            result = await session.execute(
                select(AuditLedger)
                .where(
                    AuditLedger.tenant_id == tenant_id,
                    AuditLedger.event_type.in_(
                        [
                            AuditEvent.FANOUT_FAILED.value,
                            AuditEvent.FANOUT_PARTIAL.value,
                            AuditEvent.DELIVERY_REJECTED.value,
                            AuditEvent.WEBHOOK_FAILED.value,
                        ]
                    ),
                    AuditLedger.created_at >= cutoff,
                )
                .order_by(AuditLedger.created_at.desc())
                .limit(limit)
            )
            return [e.to_dict() for e in result.scalars().all()]


async def cleanup_old_events(max_age_days: int = 30) -> int:
    """Delete audit events older than max_age_days.

    Run periodically to prevent unbounded table growth.
    Returns count of deleted events.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    async with get_db_session() as session:
        result = await session.execute(
            delete(AuditLedger).where(AuditLedger.created_at < cutoff)
        )
        deleted = result.rowcount
        if deleted:
            logger.info("Cleaned up old audit events", count=deleted)
        return deleted
