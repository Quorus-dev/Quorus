"""Audit route handlers — query message lifecycle events."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from quorus.auth.middleware import AuthContext, verify_auth

router = APIRouter(prefix="/v1/audit", tags=["audit"])
_LEGACY_TENANT = "_legacy"


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.get("/messages/{message_id}")
async def get_message_timeline(
    request: Request,
    message_id: str,
    auth: AuthContext = Depends(verify_auth),
):
    """Get the full audit timeline for a message.

    Returns all lifecycle events in chronological order:
    - message_created: Message written to history
    - message_queued: Added to outbox for fan-out
    - fanout_started: Worker began processing
    - fanout_completed: All recipients notified
    - delivered: Enqueued to recipient's inbox
    - sse_pushed: Pushed via Server-Sent Events
    - webhook_sent: Webhook notification delivered
    """
    audit_svc = request.app.state.audit_service
    if audit_svc is None:
        raise HTTPException(
            status_code=501,
            detail="Audit ledger not available (requires Postgres)",
        )

    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID format")

    events = await audit_svc.get_message_timeline(_tid(auth), msg_uuid)
    return {"message_id": message_id, "events": events}


@router.get("/recent")
async def get_recent_events(
    request: Request,
    event_type: list[str] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(verify_auth),
):
    """Get recent audit events for debugging and monitoring.

    Optionally filter by event_type (can specify multiple).
    """
    audit_svc = request.app.state.audit_service
    if audit_svc is None:
        raise HTTPException(
            status_code=501,
            detail="Audit ledger not available (requires Postgres)",
        )

    events = await audit_svc.get_recent_events(
        _tid(auth), event_types=event_type, limit=limit
    )
    return {"events": events}


@router.get("/failures")
async def get_failed_deliveries(
    request: Request,
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(verify_auth),
):
    """Get recent failed delivery events for investigation.

    Returns fanout_failed, fanout_partial, delivery_rejected,
    and webhook_failed events from the last N hours.
    """
    audit_svc = request.app.state.audit_service
    if audit_svc is None:
        raise HTTPException(
            status_code=501,
            detail="Audit ledger not available (requires Postgres)",
        )

    events = await audit_svc.get_failed_deliveries(
        _tid(auth), since_hours=hours, limit=limit
    )
    return {"events": events, "since_hours": hours}
