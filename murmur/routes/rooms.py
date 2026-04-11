"""Room lifecycle route handlers — create, list, get, join, leave, kick, destroy, rename."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request

from murmur.auth.middleware import AuthContext, verify_auth
from murmur.routes.models import (
    CreateRoomRequest,
    DestroyRoomRequest,
    JoinLeaveRequest,
    KickRequest,
    RenameRoomRequest,
)

router = APIRouter()
_LEGACY_TENANT = "_legacy"
MAX_ROOM_MEMBERS = int(os.environ.get("MAX_ROOM_MEMBERS", "50"))


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.post("/rooms")
async def create_room(
    req: CreateRoomRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    creator = auth.sub or req.created_by
    if auth.sub and req.created_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot create room as another user")
    svc = request.app.state.room_service
    result = await svc.create(_tid(auth), req.name, creator)
    await request.app.state.backends.participants.add(_tid(auth), creator)
    return result


@router.get("/rooms")
async def list_rooms(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    svc = request.app.state.room_service
    tid = _tid(auth)
    all_rooms = await svc.list_all(tid)
    # Non-legacy users only see rooms they belong to
    if auth.is_legacy or auth.role == "admin":
        return all_rooms
    result = []
    for room in all_rooms:
        members = await svc.get_members(tid, room["id"])
        if auth.sub in members:
            result.append(room)
    return result


@router.get("/rooms/{room_id}")
async def get_room(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    svc = request.app.state.room_service
    tid = _tid(auth)
    rid, data = await svc.get(tid, room_id)
    members = await svc.get_members(tid, rid)
    # Require membership to view room details (unless legacy/admin)
    if not auth.is_legacy and auth.role != "admin":
        if auth.sub not in members:
            raise HTTPException(
                status_code=403, detail="Must be a room member to view details",
            )
    return {
        "id": rid,
        "name": data.get("name", ""),
        "members": sorted(members.keys()),
        "member_roles": members,
        "created_at": data.get("created_at", ""),
    }


@router.post("/rooms/{room_id}/join")
async def join_room(
    room_id: str,
    req: JoinLeaveRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    participant = req.participant
    # Who can add members:
    # - Legacy auth: anyone (backward compat)
    # - Admin role: can add anyone
    # - Room creator: can add anyone
    # - Self-join: blocked (use invite tokens instead)
    if not auth.is_legacy and auth.role != "admin":
        room_svc = request.app.state.room_service
        rid, room_data = await room_svc.get(_tid(auth), room_id)
        is_creator = room_data.get("created_by") == auth.sub
        is_self_join = participant == auth.sub
        if not is_creator and is_self_join:
            raise HTTPException(
                status_code=403,
                detail="Self-join requires invite token. "
                "Use POST /invite/{room}/join.",
            )
        if not is_creator and not is_self_join:
            raise HTTPException(
                status_code=403,
                detail="Only room creator or admin can add members.",
            )
    svc = request.app.state.room_service
    rid, _ = await svc.get(_tid(auth), room_id)
    await svc.join(_tid(auth), rid, participant, req.role, MAX_ROOM_MEMBERS)
    await request.app.state.backends.participants.add(_tid(auth), participant)
    return {"status": "joined", "role": req.role}


@router.post("/rooms/{room_id}/leave")
async def leave_room(
    room_id: str,
    req: JoinLeaveRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    participant = auth.sub or req.participant
    if auth.sub and req.participant != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot leave room as another user")
    svc = request.app.state.room_service
    rid, _ = await svc.get(_tid(auth), room_id)
    await svc.leave(_tid(auth), rid, participant)
    return {"status": "left"}


@router.post("/rooms/{room_id}/kick")
async def kick_from_room(
    room_id: str,
    req: KickRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    requested_by = auth.sub or req.requested_by
    if auth.sub and req.requested_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot kick as another user")
    svc = request.app.state.room_service
    rid, _ = await svc.get(_tid(auth), room_id)
    is_admin = not auth.is_legacy and auth.role == "admin"
    await svc.kick(_tid(auth), rid, req.participant, requested_by, is_admin)
    return {"status": "kicked", "participant": req.participant}


@router.delete("/rooms/{room_id}")
async def destroy_room(
    room_id: str,
    req: DestroyRoomRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    requested_by = auth.sub or req.requested_by
    if auth.sub and req.requested_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot destroy room as another user")
    svc = request.app.state.room_service
    rid, _ = await svc.get(_tid(auth), room_id)
    is_admin = not auth.is_legacy and auth.role == "admin"
    room_name = await svc.destroy(_tid(auth), rid, requested_by, is_admin)
    # Also clean up room history
    backends = request.app.state.backends
    await backends.room_history.delete(_tid(auth), rid)
    return {"status": "destroyed", "room": room_name}


@router.patch("/rooms/{room_id}")
async def rename_room(
    room_id: str,
    req: RenameRoomRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    requested_by = auth.sub or req.requested_by
    if auth.sub and req.requested_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot rename room as another user")
    svc = request.app.state.room_service
    rid, _ = await svc.get(_tid(auth), room_id)
    is_admin = not auth.is_legacy and auth.role == "admin"
    old_name, new_name = await svc.rename(
        _tid(auth), rid, req.new_name, requested_by, is_admin,
    )
    # Update room name in history messages
    backends = request.app.state.backends
    await backends.room_history.rename_room_in_history(_tid(auth), rid, new_name)
    return {"status": "renamed", "old_name": old_name, "new_name": new_name}
