"""Room state endpoints — Primitive A (GET /rooms/{room}/state) and
Primitive B (POST/DELETE /rooms/{room}/lock — distributed mutex).

Primitive A returns the Shared State Matrix: goal, claimed tasks, locked
files, decisions, active agents, message counts.

Primitive B provides optimistic file locking:
  POST   /rooms/{room}/lock   — acquire a lock on a file path
  DELETE /rooms/{room}/lock/{file_path} — release a lock (requires token)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from murmur.auth.middleware import AuthContext, verify_auth

router = APIRouter()
_LEGACY_TENANT = "_legacy"
_SCHEMA_VERSION = "1.0"
_PRESENCE_TIMEOUT = 120  # seconds — agent is "active" if heartbeat within 2 min


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.get("/rooms/{room_id}/state")
async def get_room_state(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Return the Shared State Matrix for *room_id*.

    Resolves room by UUID or name.  Scoped to the caller's tenant.
    """
    room_svc = request.app.state.room_service
    backends = request.app.state.backends
    tid = _tid(auth)

    # Resolve room (raises 404 if unknown)
    rid, room_data = await room_svc.get(tid, room_id)

    # Expire stale task locks before building the snapshot
    await backends.room_state.expire_tasks(tid, rid)

    # Fetch mutable coordination state
    state = await backends.room_state.get(tid, rid)

    # Derive active_agents from presence (heartbeat within timeout, in this room)
    room_name = room_data.get("name", rid)
    presence_entries = await backends.presence.list_all(tid, _PRESENCE_TIMEOUT)
    active_agents = sorted(
        e["name"]
        for e in presence_entries
        if e.get("room") in (rid, room_name)
    )

    # Fall back to room members if no presence data
    if not active_agents:
        members = await backends.rooms.get_members(tid, rid)
        active_agents = sorted(members.keys())

    # Derive message_count and last_activity from room history
    history = await backends.room_history.get_recent(tid, rid, limit=200)
    message_count = len(history)
    if history:
        last_ts = history[-1].get("timestamp", "")
    else:
        last_ts = room_data.get("created_at", "")

    return {
        "room_id": rid,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": _SCHEMA_VERSION,
        "active_goal": state.get("active_goal"),
        "claimed_tasks": state.get("claimed_tasks", []),
        "locked_files": state.get("locked_files", {}),
        "resolved_decisions": state.get("resolved_decisions", []),
        "active_agents": active_agents,
        "message_count": message_count,
        "last_activity": last_ts,
    }
