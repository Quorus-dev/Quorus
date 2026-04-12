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
from pydantic import BaseModel, Field, field_validator

from murmur.auth.middleware import AuthContext, verify_auth
from murmur.routes.room_auth import require_room_member

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
    Requires room membership (403 if not a member).
    """
    backends = request.app.state.backends
    tid = _tid(auth)

    # Verify membership and resolve room (raises 404/403)
    rid, room_data = await require_room_member(request, auth, tid, room_id)

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
    file_path: str = Field(min_length=1, max_length=500)
    claimed_by: str
    description: str = ""
    ttl_seconds: int = 300

    @field_validator("file_path")
    @classmethod
    def no_path_traversal(cls, v: str) -> str:
        if ".." in v.split("/") or ".." in v.split("\\"):
            raise ValueError("file_path must not contain directory traversal sequences")
        return v


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
    Requires room membership (403 if not a member).
    """
    if auth.sub and body.claimed_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot claim as another user")

    backends = request.app.state.backends
    tid = _tid(auth)

    # Verify membership first (before rate limiting to avoid leaking room existence)
    rid, room_data = await require_room_member(request, auth, tid, room_id)

    # Rate limit: 30/min
    rate_limit_svc = request.app.state.rate_limit_service
    caller = auth.sub or "anon"
    if not await rate_limit_svc.check_with_limit(tid, f"lock_acquire:{caller}", 30):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    room_name = room_data.get("name", rid)
    members = room_data.get("members", {})

    # Expire stale locks first
    await backends.room_state.expire_tasks(tid, rid)

    # Grant the lock atomically (prevents TOCTOU race conditions)
    lock_token = str(uuid.uuid4())
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=body.ttl_seconds)
    ).isoformat()
    task_id = str(uuid.uuid4())

    lock_data = {
        "held_by": body.claimed_by,
        "lock_token": lock_token,
        "expires_at": expires_at,
    }
    task_data = {
        "id": task_id,
        "file_path": body.file_path,
        "claimed_by": body.claimed_by,
        "description": body.description,
        "lock_token": lock_token,
        "expires_at": expires_at,
    }

    acquired, existing = await backends.room_state.try_acquire_lock(
        tid, rid, body.file_path, lock_data, task_data
    )

    if not acquired:
        # Lock already held by another agent
        return {
            "locked": True,
            "held_by": existing.get("held_by") if existing else "unknown",
            "expires_at": existing.get("expires_at") if existing else None,
        }

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
    Requires room membership (403 if not a member).
    """
    backends = request.app.state.backends
    tid = _tid(auth)

    # Verify membership first
    rid, room_data = await require_room_member(request, auth, tid, room_id)

    # Rate limit: 30/min
    rate_limit_svc = request.app.state.rate_limit_service
    caller = auth.sub or "anon"
    if not await rate_limit_svc.check_with_limit(tid, f"lock_release:{caller}", 30):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    room_name = room_data.get("name", rid)
    members = room_data.get("members", {})

    # Release atomically with token verification (prevents TOCTOU race conditions)
    status, existing = await backends.room_state.release_lock_atomic(
        tid, rid, file_path, body.lock_token
    )

    if status == "not_found":
        raise HTTPException(status_code=404, detail="No lock held for this path")

    if status == "token_mismatch":
        raise HTTPException(status_code=403, detail="Invalid lock token")

    # Caller with valid token is the owner
    held_by = auth.sub or "unknown"
    _broadcast_lock_event(
        request, tid, rid, room_name, members,
        "LOCK_RELEASED",
        {"file_path": file_path, "held_by": held_by},
    )

    return {"released": True, "file_path": file_path}


# ---------------------------------------------------------------------------
# State write endpoints (set goal, add decision)
# ---------------------------------------------------------------------------


class SetGoalRequest(BaseModel):
    goal: str | None = Field(default=None, max_length=1000)
    set_by: str = ""


class AddDecisionRequest(BaseModel):
    decision: str = Field(min_length=1, max_length=2000)
    rationale: str | None = None


def _broadcast_state_event(
    request: Request,
    tid: str,
    rid: str,
    room_name: str,
    members: dict,
    event_type: str,
    payload: dict,
) -> None:
    """Fan-out a state change event to all room members via SSE."""
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


@router.patch("/rooms/{room_id}/state/goal")
async def set_room_goal(
    room_id: str,
    req: SetGoalRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Set (or clear) the active goal for a room.

    Pass ``null`` for goal to clear it.  Broadcasts ``GOAL_SET`` SSE event
    to all room members.  Rate limited: 10/min.
    Requires room membership (403 if not a member).
    """
    backends = request.app.state.backends
    tid = _tid(auth)

    # Verify membership first
    rid, room_data = await require_room_member(request, auth, tid, room_id)

    # Rate limit: 10/min
    rate_limit_svc = request.app.state.rate_limit_service
    caller = auth.sub or "anon"
    if not await rate_limit_svc.check_with_limit(tid, f"goal:{caller}", 10):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    room_name = room_data.get("name", rid)
    members = room_data.get("members", {})

    await backends.room_state.set_goal(tid, rid, req.goal)

    set_by = req.set_by or auth.sub or _LEGACY_TENANT
    _broadcast_state_event(
        request, tid, rid, room_name, members,
        "GOAL_SET",
        {"goal": req.goal, "set_by": set_by},
    )

    return {"active_goal": req.goal}


@router.post("/rooms/{room_id}/state/decisions")
async def add_room_decision(
    room_id: str,
    req: AddDecisionRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Record a resolved decision in the room's shared state.

    Broadcasts ``DECISION_ADDED`` SSE event to all room members.
    Rate limited: 20/min.
    Requires room membership (403 if not a member).
    """
    backends = request.app.state.backends
    tid = _tid(auth)

    # Verify membership first
    rid, room_data = await require_room_member(request, auth, tid, room_id)

    # Rate limit: 20/min
    rate_limit_svc = request.app.state.rate_limit_service
    caller = auth.sub or "anon"
    if not await rate_limit_svc.check_with_limit(tid, f"decision:{caller}", 20):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    room_name = room_data.get("name", rid)
    members = room_data.get("members", {})

    decided_by = auth.sub or _LEGACY_TENANT
    decision = {
        "id": str(uuid.uuid4()),
        "decision": req.decision,
        "decided_by": decided_by,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "rationale": req.rationale,
    }
    await backends.room_state.add_decision(tid, rid, decision)

    _broadcast_state_event(
        request, tid, rid, room_name, members,
        "DECISION_ADDED",
        {"decision": req.decision, "decided_by": decided_by},
    )

    return decision
