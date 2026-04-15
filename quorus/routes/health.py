"""Health check route handlers."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from quorus.auth.middleware import AuthContext, verify_auth

router = APIRouter()
MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
REDIS_URL = os.environ.get("REDIS_URL", "")
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))
_LEGACY_TENANT = "_legacy"

_start_time = time.time()


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.get("/health")
async def health():
    """Check relay health — returns 503 if required deps are down."""
    from quorus.backends.redis_client import check_redis
    from quorus.storage.postgres import check_connection

    checks: dict = {"status": "ok"}

    # Postgres check
    pg_ok = await check_connection()
    if pg_ok:
        checks["postgres"] = "connected"
    elif DATABASE_URL:
        # Postgres required in production — mark unhealthy
        checks["status"] = "unhealthy"
        checks["postgres"] = "disconnected"
    else:
        # Dev mode: check file persistence
        persist_dir = os.path.dirname(os.path.abspath(MESSAGES_FILE)) or "."
        if os.access(persist_dir, os.W_OK):
            checks["persistence"] = "ok"
        else:
            checks["status"] = "degraded"
            checks["persistence"] = "directory not writable"

    # Redis check
    redis_ok = await check_redis()
    if redis_ok:
        checks["redis"] = "connected"
    elif REDIS_URL:
        # Redis required in production — mark unhealthy
        checks["status"] = "unhealthy"
        checks["redis"] = "disconnected"

    status_code = 200 if checks["status"] == "ok" else 503
    return JSONResponse(checks, status_code=status_code)


@router.get("/health/detailed")
async def health_detailed(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    from quorus.auth.middleware import require_role

    # Admin-only — prevents leaking operational details to regular users
    require_role(auth, "admin")
    tid = _tid(auth)
    backends = request.app.state.backends
    total_msgs = await backends.messages.count_all(tid)
    rooms_count = await backends.rooms.count(tid)
    # Tenant-scoped participant count (not global)
    all_participants = await backends.participants.list_all(tid)
    participants_count = len(all_participants)

    presence_svc = request.app.state.presence_service
    online = await presence_svc.list_all(tid, HEARTBEAT_TIMEOUT)

    analytics_svc = request.app.state.analytics_service
    stats = await analytics_svc.get_stats(tid)

    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _start_time),
        "rooms": rooms_count,
        "participants": participants_count,
        "pending_messages": total_msgs,
        "online_agents": len(online),
        "total_sent": stats.get("total_sent", 0),
        "total_delivered": stats.get("total_delivered", 0),
    }
