"""DM route handlers — send, fetch (with long-poll), ACK, and peek."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from murmur.auth.middleware import AuthContext, require_identity, verify_auth
from murmur.routes.models import AckRequest, SendMessageRequest

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
    # Track participants via backend
    await request.app.state.backends.participants.add(_tid(auth), sender, msg.to)
    return result


@router.get("/messages/{recipient}")
async def get_messages(
    recipient: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    wait: int = 0,
    ack: str = "auto",
):
    """Fetch pending messages for *recipient*.

    Query params:
    - ``wait``: long-poll timeout in seconds (0-60)
    - ``ack``: ``"auto"`` (default) auto-acknowledges on response,
      ``"manual"`` returns an ``ack_token`` for client-side ACK via
      ``POST /messages/{recipient}/ack``.  Unacked messages are
      redelivered after the visibility timeout.
    """
    require_identity(auth, recipient)
    svc = request.app.state.message_service
    result = await svc.fetch(_tid(auth), recipient, wait)

    if ack == "manual":
        # Return messages + ack_token for client-side ACK
        return {
            "messages": result.messages,
            "ack_token": result.ack_token,
        }

    # Default: server-side ACK (backward compatible)
    await result.ack()
    return result.messages


@router.post("/messages/{recipient}/ack")
async def ack_messages(
    recipient: str,
    req: AckRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Acknowledge receipt of messages (client-side ACK).

    Provide either ``ack_token`` (from a ``?ack=manual`` fetch) or
    specific ``message_ids`` to acknowledge individual messages.
    Unacknowledged messages are redelivered after the visibility timeout.
    """
    require_identity(auth, recipient)
    svc = request.app.state.message_service
    tid = _tid(auth)

    if req.ack_token:
        await svc.ack_by_token(tid, recipient, req.ack_token)
        return {"status": "acked"}
    if req.message_ids:
        count = await svc.ack_by_ids(tid, recipient, req.message_ids)
        return {"status": "acked", "count": count}
    raise HTTPException(
        status_code=400, detail="Provide ack_token or message_ids"
    )


@router.get("/messages/{recipient}/peek")
async def peek_messages(
    recipient: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    require_identity(auth, recipient)
    svc = request.app.state.message_service
    count = await svc.peek(_tid(auth), recipient)
    pending = await svc.pending(_tid(auth), recipient)
    return {"count": count, "pending": pending, "recipient": recipient}


@router.get("/participants")
async def list_participants_endpoint(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    return await request.app.state.backends.participants.list_all(_tid(auth))
