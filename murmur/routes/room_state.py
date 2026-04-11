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


# ---------------------------------------------------------------------------
# Primitive B — Distributed Mutex (file-path lock)
# ---------------------------------------------------------------------------


class ClaimTaskRequest(BaseModel):
    file_path: str
    claimed_by: str
    description: str = ""
    ttl_seconds: int = 300


class ReleaseTaskRequest(BaseModel):
    lock_token: str


def _broadcast_lock_event(
    request: Request,
    tid: str,
    rid: str,
    room_name: str,
    members: dict,
    event_type: str,
    payload: dict,
) -> None:
    """Fan-out a lock lifecycle event to all room members via SSE."""
    sse_svc = request.app.state.sse_service
    ts = datetime.now(timezone.utc).isoformat()
    for recipient in members:
        msg = {
            "id": str(uuid.uuid4()),
            "from_name": "_system",
            "to": recipient,
            "room": room_name,
            "content": str(payload),
            "message_type": event_type,
            "timestamp": ts,
        }
        sse_svc.push(tid, recipient, msg)


@router.post("/rooms/{room_id}/lock")
async def claim_task(
    room_id: str,
    body: ClaimTaskRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Acquire an optimistic file lock (Primitive B).

    Returns ``{"locked": false, "lock_token": uuid, "expires_at": ISO}`` if
    the lock was granted, or ``{"locked": true, "held_by": str, "expires_at": ISO}``
    if another agent already holds it.

    On grant, SSE-broadcasts ``LOCK_ACQUIRED`` to all room members.
    """
    if auth.sub and body.claimed_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot claim as another user")

    room_svc = request.app.state.room_service
    backends = request.app.state.backends
    tid = _tid(auth)

    rid, room_data = await room_svc.get(tid, room_id)
    room_name = room_data.get("name", rid)
    members = room_data.get("members", {})

    # Expire stale locks first
    await backends.room_state.expire_tasks(tid, rid)

    state = await backends.room_state.get(tid, rid)
    existing = state.get("locked_files", {}).get(body.file_path)

    if existing:
        return {
            "locked": True,
            "held_by": existing.get("held_by"),
            "expires_at": existing.get("expires_at"),
        }

    # Grant the lock
    lock_token = str(uuid.uuid4())
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=body.ttl_seconds)
    ).isoformat()
    task_id = str(uuid.uuid4())

    task = {
        "id": task_id,
        "file_path": body.file_path,
        "claimed_by": body.claimed_by,
        "description": body.description,
        "lock_token": lock_token,
        "expires_at": expires_at,
    }
    await backends.room_state.add_claimed_task(tid, rid, task)

    _broadcast_lock_event(
        request, tid, rid, room_name, members,
        "LOCK_ACQUIRED",
        {"file_path": body.file_path, "held_by": body.claimed_by, "expires_at": expires_at},
    )

    return {
        "locked": False,
        "lock_token": lock_token,
        "expires_at": expires_at,
    }


@router.delete("/rooms/{room_id}/lock/{file_path:path}")
async def release_task(
    room_id: str,
    file_path: str,
    body: ReleaseTaskRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Release a previously acquired file lock (Primitive B).

    Validates ``lock_token`` ownership.  Returns 403 if the token does not
    match the current lock holder, 404 if no lock exists for the path.

    On success, SSE-broadcasts ``LOCK_RELEASED`` to all room members.
    """
    room_svc = request.app.state.room_service
    backends = request.app.state.backends
    tid = _tid(auth)

    rid, room_data = await room_svc.get(tid, room_id)
    room_name = room_data.get("name", rid)
    members = room_data.get("members", {})

    state = await backends.room_state.get(tid, rid)
    existing = state.get("locked_files", {}).get(file_path)

    if not existing:
        raise HTTPException(status_code=404, detail="No lock held for this path")

    if existing.get("lock_token") != body.lock_token:
        raise HTTPException(status_code=403, detail="Invalid lock token")

    await backends.room_state.release_lock(tid, rid, file_path)

    # Also remove from claimed_tasks list
    tasks = state.get("claimed_tasks", [])
    for task in tasks:
        if task.get("file_path") == file_path and task.get("lock_token") == body.lock_token:
            await backends.room_state.remove_claimed_task(tid, rid, task["id"])
            break

    _broadcast_lock_event(
        request, tid, rid, room_name, members,
        "LOCK_RELEASED",
        {"file_path": file_path, "held_by": existing.get("held_by")},
    )

    return {"released": True, "file_path": file_path}
