"""DM route handlers — send, fetch (with long-poll), and peek."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from murmur.auth.middleware import AuthContext, require_identity, verify_auth
from murmur.routes.analytics import track_delivery, track_send
from murmur.routes.models import SendMessageRequest

router = APIRouter()
_LEGACY_TENANT = "_legacy"


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.post("/messages")
async def send_message(
    msg: SendMessageRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    sender = auth.sub or msg.from_name
    if auth.sub and msg.from_name != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot send as another user")
    svc = request.app.state.message_service
    result = await svc.send_dm(_tid(auth), sender, msg.to, msg.content)
    # Track participants and analytics
    request.app.state.backends.participants.update({sender, msg.to})
    track_send(sender)
    return result


@router.get("/messages/{recipient}")
async def get_messages(
    recipient: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    wait: int = 0,
):
    require_identity(auth, recipient)
    svc = request.app.state.message_service
    messages = await svc.fetch(_tid(auth), recipient, wait)
    if messages:
        track_delivery(recipient, len(messages))
    return messages


@router.get("/messages/{recipient}/peek")
async def peek_messages(
    recipient: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    require_identity(auth, recipient)
    svc = request.app.state.message_service
    count = await svc.peek(_tid(auth), recipient)
    return {"count": count, "recipient": recipient}


@router.get("/participants")
async def list_participants_endpoint(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    return sorted(request.app.state.backends.participants)
