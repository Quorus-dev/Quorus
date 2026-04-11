"""Health check route handlers."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from murmur.auth.middleware import AuthContext, verify_auth
from murmur.routes.analytics import _analytics, _start_time

router = APIRouter()
MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))


@router.get("/health")
async def health():
    """Check relay health including Postgres connectivity."""
    from murmur.storage.postgres import check_connection

    checks: dict = {"status": "ok"}

    pg_ok = await check_connection()
    if pg_ok:
        checks["postgres"] = "connected"
    else:
        persist_dir = os.path.dirname(os.path.abspath(MESSAGES_FILE)) or "."
        if not os.access(persist_dir, os.W_OK):
            checks["status"] = "degraded"
            checks["persistence"] = "directory not writable"
        else:
            checks["persistence"] = "ok"

    status_code = 200 if checks["status"] == "ok" else 503
    return JSONResponse(checks, status_code=status_code)


@router.get("/health/detailed")
async def health_detailed(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    backends = request.app.state.backends
    total_msgs = sum(len(msgs) for msgs in backends.messages._queues.values())
    sse_svc = request.app.state.sse_service
    total_sse = sum(len(qs) for qs in sse_svc._queues.values())

    # Count rooms
    rooms_count = len(backends.rooms._rooms)

    # Count online agents
    presence_svc = request.app.state.presence_service
    online = await presence_svc.list_all("_legacy", HEARTBEAT_TIMEOUT)
    online_agents = len(online)

    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _start_time),
        "rooms": rooms_count,
        "participants": len(backends.participants),
        "pending_messages": total_msgs,
        "active_sse_connections": total_sse,
        "online_agents": online_agents,
        "total_sent": _analytics["total_sent"],
        "total_delivered": _analytics["total_delivered"],
    }
