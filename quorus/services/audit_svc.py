"""Audit service — records message lifecycle events for tracing."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import delete, select, text

from quorus.auth.policy import PolicyContext, PolicyResult
from quorus.models.audit import AuditEvent, AuditLedger
from quorus.storage.postgres import get_db_session

logger = structlog.get_logger("quorus.services.audit")
_REDACTED = "[redacted]"
_SECRET_KEYS = ("api_key", "auth", "password", "secret", "token")


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(secret in key_text for secret in _SECRET_KEYS):
                redacted[str(key)] = _REDACTED
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _canonical_entry_payload(entry: AuditLedger, prev_hash: str | None) -> str:
    payload = {
        "tenant_id": entry.tenant_id,
        "message_id": str(entry.message_id),
        "room_id": entry.room_id,
        "room_name": entry.room_name,
        "event_type": entry.event_type,
        "actor": entry.actor,
        "target": entry.target,
        "details": entry.details,
        "error": entry.error,
        "created_at": _utc_iso(entry.created_at),
        "prev_hash": prev_hash,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _entry_hash(entry: AuditLedger, prev_hash: str | None) -> str:
    canonical = _canonical_entry_payload(entry, prev_hash)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _receipt_signature(entry_hash: str) -> str | None:
    secret = os.environ.get("AUDIT_RECEIPT_SECRET") or os.environ.get("RELAY_SECRET")
    if not secret:
        return None
    return hmac.new(
        secret.encode("utf-8"),
        entry_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


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

    async def _lock_tenant_chain(self, session: Any, tenant_id: str) -> None:
        """Serialize per-tenant chain writes on Postgres when available."""
        try:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id))"),
                {"tenant_id": tenant_id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Audit chain advisory lock unavailable", error=str(exc))

    async def _latest_hash(self, session: Any, tenant_id: str) -> str | None:
        result = await session.execute(
            select(AuditLedger.entry_hash)
            .where(
                AuditLedger.tenant_id == tenant_id,
                AuditLedger.entry_hash.isnot(None),
            )
            .order_by(AuditLedger.created_at.desc(), AuditLedger.id.desc())
            .limit(1)
        )
        value = result.scalar_one_or_none()
        return str(value) if value else None

    def _seal_entry(self, event: AuditLedger, prev_hash: str | None) -> str:
        if event.created_at is None:
            event.created_at = datetime.now(timezone.utc)
        event.prev_hash = prev_hash
        event.entry_hash = _entry_hash(event, prev_hash)
        event.receipt_signature = _receipt_signature(event.entry_hash)
        return event.entry_hash

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
        session: Any | None = None,
    ) -> AuditLedger:
        """Record an audit event.

        Exceptions propagate. Per CLAUDE.md Lessons Learned ("Audit Service
        Must Propagate Exceptions"): if audit write fails and business commit
        succeeds, that's an unaudited PHI mutation — compliance breach. The
        caller is expected to wrap business logic + this call in a single
        transaction so they either both succeed or both fail.

        ``session`` parameter: when provided, we add the event to that session
        instead of opening a new one. This prevents split-brain where an outer
        worker session rolls back but the nested audit session already
        committed (see outbox_svc usage).
        """
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

        if session is not None:
            # Caller owns the transaction — join it.
            await self._lock_tenant_chain(session, tenant_id)
            self._seal_entry(event, await self._latest_hash(session, tenant_id))
            session.add(event)
        else:
            async with get_db_session() as new_session:
                await self._lock_tenant_chain(new_session, tenant_id)
                self._seal_entry(event, await self._latest_hash(new_session, tenant_id))
                new_session.add(event)

        logger.debug(
            "Audit event recorded",
            message_id=str(message_id),
            event_type=event_type,
            actor=actor,
        )
        return event

    async def record_batch(
        self,
        tenant_id: str,
        message_id: uuid.UUID,
        events: list[dict],
        session: Any | None = None,
    ) -> list[AuditLedger]:
        """Record multiple audit events for a message. Exceptions propagate.

        ``session`` param: see ``record()`` — same split-brain-prevention
        semantics.
        """
        async def _add(sess: Any) -> list[AuditLedger]:
            await self._lock_tenant_chain(sess, tenant_id)
            prev_hash = await self._latest_hash(sess, tenant_id)
            added: list[AuditLedger] = []
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
                prev_hash = self._seal_entry(event, prev_hash)
                sess.add(event)
                added.append(event)
            return added

        if session is not None:
            added_events = await _add(session)
        else:
            async with get_db_session() as new_session:
                added_events = await _add(new_session)

        logger.debug(
            "Audit events recorded",
            message_id=str(message_id),
            count=len(events),
        )
        return added_events

    async def record_mcp_tool_call(
        self,
        tenant_id: str,
        actor: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        mutating: bool,
    ) -> dict:
        """Record an MCP tool call before the tool performs relay mutation."""
        event = await self.record(
            tenant_id=tenant_id,
            message_id=uuid.uuid4(),
            event_type=AuditEvent.MCP_TOOL_CALLED,
            actor=actor,
            target=tool_name,
            details={
                "tool_name": tool_name,
                "arguments": _redact(arguments),
                "mutating": mutating,
            },
        )
        return {
            "id": str(event.id),
            "entry_hash": event.entry_hash,
            "prev_hash": event.prev_hash,
            "receipt_signature": event.receipt_signature,
        }

    async def record_policy_evaluation(
        self,
        tenant_id: str,
        ctx: PolicyContext,
        result: PolicyResult,
        session: Any | None = None,
    ) -> dict:
        """Record a policy engine decision before the gated mutation runs."""
        event = await self.record(
            tenant_id=tenant_id,
            message_id=uuid.uuid4(),
            event_type=AuditEvent.POLICY_EVALUATED,
            actor=ctx.actor,
            target=ctx.action,
            details={
                "action": ctx.action,
                "resource": ctx.resource,
                "role": ctx.role,
                "rule_id": result.rule_id,
                "decision": result.decision.value,
                "effective_decision": result.effective_decision.value,
                "reason": result.reason,
                "mode": result.mode.value,
                "extra": _redact(ctx.extra or {}),
            },
            session=session,
        )
        return {
            "id": str(event.id),
            "entry_hash": event.entry_hash,
            "prev_hash": event.prev_hash,
            "receipt_signature": event.receipt_signature,
        }

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

    async def verify_chain(self, tenant_id: str, limit: int = 10000) -> dict:
        """Walk the tenant audit hash chain and report the first break."""
        async with get_db_session() as session:
            result = await session.execute(
                select(AuditLedger)
                .where(AuditLedger.tenant_id == tenant_id)
                .order_by(AuditLedger.created_at.asc(), AuditLedger.id.asc())
                .limit(limit)
            )
            events = result.scalars().all()

        prev_hash: str | None = None
        checked = 0
        legacy_entries = 0
        for index, event in enumerate(events):
            if not event.entry_hash:
                legacy_entries += 1
                continue
            checked += 1
            if event.prev_hash != prev_hash:
                return {
                    "verified": False,
                    "checked_entries": checked,
                    "legacy_entries": legacy_entries,
                    "failed_at": index,
                    "entry_id": str(event.id),
                    "reason": "prev_hash mismatch",
                    "expected_prev_hash": prev_hash,
                    "actual_prev_hash": event.prev_hash,
                }
            expected_hash = _entry_hash(event, event.prev_hash)
            if event.entry_hash != expected_hash:
                return {
                    "verified": False,
                    "checked_entries": checked,
                    "legacy_entries": legacy_entries,
                    "failed_at": index,
                    "entry_id": str(event.id),
                    "reason": "entry_hash mismatch",
                    "expected_entry_hash": expected_hash,
                    "actual_entry_hash": event.entry_hash,
                }
            prev_hash = event.entry_hash

        return {
            "verified": True,
            "checked_entries": checked,
            "legacy_entries": legacy_entries,
            "last_hash": prev_hash,
            "truncated": len(events) == limit,
        }


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
