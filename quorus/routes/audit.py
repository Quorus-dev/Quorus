"""Audit route handlers — query message lifecycle events."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from quorus.auth.middleware import AuthContext, verify_auth

# Wave-5 Fix 7 — cap on the size of the ``arguments`` payload.
# A compromised MCP agent could otherwise post arbitrarily large dicts to
# DoS the audit ledger / Postgres write path. 4 KB easily fits any
# legitimate tool invocation while bounding worst-case write cost.
_MAX_MCP_ARGUMENTS_BYTES = 4096

router = APIRouter(prefix="/v1/audit", tags=["audit"])
_LEGACY_TENANT = "_legacy"


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


class McpToolAuditRequest(BaseModel):
    """Audit envelope emitted by local MCP servers before tool execution."""

    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    mutating: bool = False


def _audit_service(request: Request):
    audit_svc = request.app.state.audit_service
    if audit_svc is None:
        raise HTTPException(
            status_code=501,
            detail="Audit ledger not available (requires Postgres)",
        )
    return audit_svc


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
    audit_svc = _audit_service(request)

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
    audit_svc = _audit_service(request)

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
    audit_svc = _audit_service(request)

    events = await audit_svc.get_failed_deliveries(
        _tid(auth), since_hours=hours, limit=limit
    )
    return {"events": events, "since_hours": hours}


@router.post("/mcp-tool-call")
async def record_mcp_tool_call(
    req: McpToolAuditRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Record an MCP tool call before the local MCP server executes it.

    Wave-5 Fix 7 — the ``arguments`` payload is capped at 4 KB BEFORE any
    DB write so a malicious / compromised agent cannot DoS the ledger by
    flooding it with multi-MB tool arguments.
    """
    # Compute size on the validated dict, NOT the raw body, so we measure
    # what actually gets persisted (not pretty-printing whitespace).
    try:
        arg_bytes = len(json.dumps(req.arguments, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        # Unserializable input is treated as oversized — pydantic should
        # have caught most non-serializable values, but defense-in-depth.
        raise HTTPException(
            status_code=413,
            detail="arguments payload is not JSON-serializable",
        ) from None
    if arg_bytes > _MAX_MCP_ARGUMENTS_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"arguments payload exceeds {_MAX_MCP_ARGUMENTS_BYTES // 1024}KB "
                f"(got {arg_bytes} bytes)"
            ),
        )
    audit_svc = _audit_service(request)
    return await audit_svc.record_mcp_tool_call(
        tenant_id=_tid(auth),
        actor=auth.sub or "legacy-admin",
        tool_name=req.tool_name,
        arguments=req.arguments,
        mutating=req.mutating,
    )


@router.get("/verify")
async def verify_audit_chain(
    request: Request,
    limit: int = Query(default=10000, ge=1, le=100000),
    auth: AuthContext = Depends(verify_auth),
):
    """Verify the tenant's append-only audit hash chain."""
    audit_svc = _audit_service(request)
    return await audit_svc.verify_chain(_tid(auth), limit=limit)
