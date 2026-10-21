"""Presence / heartbeat route handlers."""

from __future__ import annotations

import json
import os
import time

from fastapi import APIRouter, Depends, Request

from quorus import limit_status
from quorus.auth.middleware import AuthContext, require_identity, verify_auth
from quorus.auth.name_reservations import reserve_names
from quorus.routes.models import HeartbeatRequest
from quorus.services import presence_trail

router = APIRouter()
_LEGACY_TENANT = "_legacy"
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))


def _float(value: object) -> float:
    try:
        return float(value or 0)  # Redis stores it as a string
    except (TypeError, ValueError):
        return 0.0


def _limits(stored: object) -> dict:
    """The stored ``pause`` / ``readings``, still valid now; ``{}`` if none."""
    if not stored or not isinstance(stored, str):
        return {}
    try:
        raw = json.loads(stored)
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return limit_status.clean(raw.get("pause"), raw.get("readings"), time.time())


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.post("/heartbeat")
async def heartbeat(
    req: HeartbeatRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    require_identity(auth, req.instance_name)
    await reserve_names(request, auth, _tid(auth), req.instance_name)
    svc = request.app.state.presence_service
    limits = limit_status.clean(req.pause, req.readings, time.time())
    entry = await svc.heartbeat(_tid(auth), req.instance_name, req.status, req.room,
                                reason=req.reason, activity=req.activity,
                                waiting_on=req.waiting_on, waiting_until=req.waiting_until,
                                model=req.model, limits=json.dumps(limits) if limits else "")
    await request.app.state.backends.participants.add(_tid(auth), req.instance_name)
    return {"status": "ok", "timestamp": entry.get("last_heartbeat", "")}


@router.get("/presence")
async def get_presence(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    trail: bool = False,
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
            "activity": (e.get("activity") or "") if online and e.get("status") == "busy" else "",
            "last_heartbeat": e.get("last_heartbeat", ""),
            "uptime_start": e.get("uptime_start", ""),
            "reason": e.get("reason", ""),
            "last_wake_at": e.get("last_wake_at", ""),
            "waiting_on": (e.get("waiting_on") or "") if online else "",
            "waiting_until": _float(e.get("waiting_until")) if online else 0.0,
            "model": e.get("model") or "",
            # Checked again on read: a reading that expired since it was stored
            # (its reset passed, or its pause ended) is not shown.
            **(_limits(e.get("limits")) if online else {}),
            # ?trail=1: the last steps (mission-control panels), same field as
            # `activity`, so no new exposure; off by default to keep it small.
            **({"trail": presence_trail.public(e.get("trail"))} if trail else {}),
        })
    result.sort(key=lambda x: (not x["online"], x["name"]))
    return result
