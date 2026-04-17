"""Presence / heartbeat route handlers."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from quorus.auth.middleware import AuthContext, require_identity, verify_auth
from quorus.routes.models import HeartbeatRequest

router = APIRouter()
_LEGACY_TENANT = "_legacy"
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.post("/heartbeat")
async def heartbeat(
    req: HeartbeatRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    require_identity(auth, req.instance_name)
    svc = request.app.state.presence_service
    entry = await svc.heartbeat(_tid(auth), req.instance_name, req.status, req.room)
    await request.app.state.backends.participants.add(_tid(auth), req.instance_name)
    return {"status": "ok", "timestamp": entry.get("last_heartbeat", "")}


@router.get("/presence")
async def get_presence(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    svc = request.app.state.presence_service
    # Single list_all call — the backend already returns every entry with
    # an ``_online`` flag classified against HEARTBEAT_TIMEOUT. The prior
    # two-call pattern (once for online, once with a 100-year timeout for
    # all entries) created a second cache bucket that never hit, defeating
    # the 5s presence cache for every dashboard refresh.
    entries = await svc.list_all(_tid(auth), HEARTBEAT_TIMEOUT)
    result = []
    for e in entries:
        online = bool(e.get("_online"))
        result.append({
            "name": e.get("name", ""),
            "online": online,
            "status": e.get("status", "active") if online else "offline",
            "room": e.get("room", ""),
            "last_heartbeat": e.get("last_heartbeat", ""),
            "uptime_start": e.get("uptime_start", ""),
        })
    result.sort(key=lambda x: (not x["online"], x["name"]))
    return result
