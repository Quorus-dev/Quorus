"""Health check route handlers."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from murmur.auth.middleware import AuthContext, verify_auth

router = APIRouter()
MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))

_start_time = time.time()


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

    # Check Redis health if available
    from murmur.backends.redis_client import check_redis

    redis_ok = await check_redis()
    if redis_ok:
        checks["redis"] = "connected"

    status_code = 200 if checks["status"] == "ok" else 503
    return JSONResponse(checks, status_code=status_code)


@router.get("/health/detailed")
async def health_detailed(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    backends = request.app.state.backends
    total_msgs = await backends.messages.count_all_global()

    # Count rooms
    rooms_count = await backends.rooms.count_global()

    # Count participants
    participants_count = await backends.participants.count_global()

    # Count online agents
    presence_svc = request.app.state.presence_service
    online = await presence_svc.list_all("_legacy", HEARTBEAT_TIMEOUT)
    online_agents = len(online)

    # Analytics stats
    analytics_svc = request.app.state.analytics_service
    stats = await analytics_svc.get_stats("_legacy")

    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _start_time),
        "rooms": rooms_count,
        "participants": participants_count,
        "pending_messages": total_msgs,
        "online_agents": online_agents,
        "total_sent": stats.get("total_sent", 0),
        "total_delivered": stats.get("total_delivered", 0),
    }
