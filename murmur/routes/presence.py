"""Presence / heartbeat route handlers."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from murmur.auth.middleware import AuthContext, require_identity, verify_auth
from murmur.routes.models import HeartbeatRequest

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
    entries = await svc.list_all(_tid(auth), HEARTBEAT_TIMEOUT)
    # The backend returns entries within timeout (online).
    # We also want offline ones, so we get all with a very large timeout.
    all_entries = await svc.list_all(_tid(auth), timeout=86400 * 365 * 100)
    online_names = {e["name"] for e in entries}
    result = []
    for e in all_entries:
        name = e.get("name", "")
        online = name in online_names
        result.append({
            "name": name,
            "online": online,
            "status": e.get("status", "active") if online else "offline",
            "room": e.get("room", ""),
            "last_heartbeat": e.get("last_heartbeat", ""),
            "uptime_start": e.get("uptime_start", ""),
        })
    result.sort(key=lambda x: (not x["online"], x["name"]))
    return result
