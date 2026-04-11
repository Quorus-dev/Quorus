"""Room message route handlers — send (with fan-out), history, and search."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request

from murmur.auth.middleware import AuthContext, verify_auth
from murmur.routes.models import RoomMessageRequest

MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))

router = APIRouter()
_LEGACY_TENANT = "_legacy"


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


async def _require_room_member(
    request: Request, auth: AuthContext, tid: str, room_id: str,
) -> None:
    """Raise 403 if the caller is not a member of the room."""
    if auth.is_legacy:
        return
    svc = request.app.state.room_service
    rid, _ = await svc.get(tid, room_id)
    members = await svc.get_members(tid, rid)
    if auth.sub not in members:
        raise HTTPException(
            status_code=403, detail="Must be a room member",
        )


@router.post("/rooms/{room_id}/messages")
async def send_room_message(
    room_id: str,
    msg: RoomMessageRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    sender = auth.sub or msg.from_name
    if auth.sub and msg.from_name != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot send as another user")
    if len(msg.content.encode("utf-8")) > MAX_MESSAGE_SIZE:
        raise HTTPException(status_code=413, detail="Message content exceeds maximum size")
    svc = request.app.state.room_msg_service
    result = await svc.send(
        _tid(auth), room_id, sender, msg.content,
        message_type=msg.message_type, reply_to=msg.reply_to,
    )
    await request.app.state.backends.participants.add(_tid(auth), sender)
    return result


@router.get("/rooms/{room_id}/history")
async def get_room_history(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    limit: int = 50,
):
    tid = _tid(auth)
    await _require_room_member(request, auth, tid, room_id)
    svc = request.app.state.room_msg_service
    return await svc.history(tid, room_id, limit)


@router.get("/rooms/{room_id}/thread/{message_id}")
async def get_message_thread(
    room_id: str,
    message_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Return the parent message and all its replies.

    Returns 404 if the message_id is not found in the room.
    """
    tid = _tid(auth)
    await _require_room_member(request, auth, tid, room_id)
    svc = request.app.state.room_msg_service
    thread = await svc.get_thread(tid, room_id, message_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Message not found")
    return thread


@router.get("/rooms/{room_id}/search")
async def search_room_history(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    q: str = "",
    sender: str = "",
    message_type: str = "",
    limit: int = 50,
):
    tid = _tid(auth)
    await _require_room_member(request, auth, tid, room_id)
    svc = request.app.state.room_msg_service
    return await svc.search(
        tid, room_id, q=q, sender=sender,
        message_type=message_type, limit=limit,
    )
