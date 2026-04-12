"""DM route handlers — send, fetch (with long-poll), ACK, and peek."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from murmur.auth.middleware import AuthContext, require_identity, verify_auth
from murmur.routes.models import AckRequest, SendMessageRequest

router = APIRouter()
_LEGACY_TENANT = "_legacy"
_IDEMPOTENCY_TTL = int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", "300"))
_MAX_DM_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.post("/messages")
async def send_message(
    msg: SendMessageRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """Send a direct message.

    Headers:
    - ``Idempotency-Key``: optional key to deduplicate retried requests.
      If provided, repeated sends with the same key within the TTL window
      (default 5 minutes) return the original response without re-sending.
      Concurrent requests with the same key return 409 Conflict.
    """
    sender = auth.sub or msg.from_name
    if auth.sub and msg.from_name != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot send as another user")
    if len(msg.content.encode("utf-8")) > _MAX_DM_SIZE:
        raise HTTPException(status_code=413, detail="Message content exceeds maximum size")

    tid = _tid(auth)

    # Rate limit: 30/min per sender
    rate_limit_svc = request.app.state.rate_limit_service
    if not await rate_limit_svc.check_with_limit(tid, f"dm_send:{sender}", 30):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    backends = request.app.state.backends
    idempotency_cache_key = f"dm:{idempotency_key}" if idempotency_key else None

    # Atomically reserve the idempotency key (prevents race conditions)
    if idempotency_cache_key:
        cached = await backends.idempotency.reserve(
            tid, idempotency_cache_key, _IDEMPOTENCY_TTL
        )
        if cached:
            return cached

    svc = request.app.state.message_service
    try:
        result = await svc.send_dm(tid, sender, msg.to, msg.content)
    except Exception:
        # Release the pending key so retries aren't blocked by 409
        if idempotency_cache_key:
            await backends.idempotency.delete(tid, idempotency_cache_key)
        raise

    # Store result in idempotency cache (replaces pending marker)
    if idempotency_cache_key:
        await backends.idempotency.set(
            tid, idempotency_cache_key, result, _IDEMPOTENCY_TTL
        )

    # Track participants via backend
    await backends.participants.add(tid, sender, msg.to)
    return result


@router.get("/messages/{recipient}")
async def get_messages(
    recipient: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    wait: int = 0,
    ack: str = "manual",
):
    """Fetch pending messages for *recipient*.

    Query params:
    - ``wait``: long-poll timeout in seconds (0-60)
    - ``ack``: acknowledgment mode:
      - ``"manual"`` (default): returns ``ack_token`` for client-side ACK
        via ``POST /messages/{recipient}/ack``. Unacked messages are
        redelivered after the visibility timeout. **Recommended for
        at-least-once delivery.**
      - ``"server"``: auto-acknowledges on response. Messages are deleted
        before the client processes them — use only for dev/testing.
      - ``"auto"``: legacy alias for ``"server"``.
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

    # Server-side ACK (legacy "auto" or explicit "server")
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
    specific ``delivery_ids`` (from the ``_delivery_id`` field in
    each message) to acknowledge individual messages.
    Unacknowledged messages are redelivered after the visibility timeout.
    """
    require_identity(auth, recipient)
    svc = request.app.state.message_service
    tid = _tid(auth)

    if req.ack_token:
        await svc.ack_by_token(tid, recipient, req.ack_token)
        return {"status": "acked"}
    if req.delivery_ids:
        count = await svc.ack_by_ids(tid, recipient, req.delivery_ids)
        return {"status": "acked", "count": count}
    raise HTTPException(
        status_code=400, detail="Provide ack_token or delivery_ids"
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
