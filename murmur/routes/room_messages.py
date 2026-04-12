"""Room message route handlers — send (with fan-out), history, and search."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from murmur.auth.middleware import AuthContext, verify_auth
from murmur.routes.models import RoomMessageRequest

MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))
_IDEMPOTENCY_TTL = int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", "300"))

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
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """Send a message to a room.

    Headers:
    - ``Idempotency-Key``: optional key to deduplicate retried requests.
      If provided, repeated sends with the same key within the TTL window
      (default 5 minutes) return the original response without re-sending.
    """
    sender = auth.sub or msg.from_name
    if auth.sub and msg.from_name != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot send as another user")
    if len(msg.content.encode("utf-8")) > MAX_MESSAGE_SIZE:
        raise HTTPException(status_code=413, detail="Message content exceeds maximum size")

    tid = _tid(auth)
    backends = request.app.state.backends
    idempotency_cache_key = f"room:{room_id}:{idempotency_key}" if idempotency_key else None

    # Check idempotency key for cached result
    if idempotency_cache_key:
        cached = await backends.idempotency.get(tid, idempotency_cache_key)
        if cached:
            return cached

    svc = request.app.state.room_msg_service
    result = await svc.send(
        tid, room_id, sender, msg.content,
        message_type=msg.message_type, reply_to=msg.reply_to,
    )

    # Store result in idempotency cache
    if idempotency_cache_key:
        await backends.idempotency.set(
            tid, idempotency_cache_key, result, _IDEMPOTENCY_TTL
        )

    await backends.participants.add(tid, sender)
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
