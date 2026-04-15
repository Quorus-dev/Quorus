"""DM route handlers — send, fetch (with long-poll), ACK, and peek."""

from __future__ import annotations

import hashlib
import json
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from quorus.auth.middleware import AuthContext, require_identity, verify_auth
from quorus.routes.models import AckRequest, SendMessageRequest

router = APIRouter()
_logger = logging.getLogger(__name__)
_LEGACY_TENANT = "_legacy"
_IDEMPOTENCY_TTL = int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", "300"))
_MAX_DM_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))  # 51 KB


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def _body_fingerprint(body: dict) -> str:
    """Create a short fingerprint of the request body for idempotency binding."""
    # Sort keys for deterministic hashing
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


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
      The key is bound to the request body — different content with the
      same key is treated as a new request.
    """
    sender = auth.sub or msg.from_name
    if auth.sub and msg.from_name != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot send as another user")
    if len(msg.content.encode("utf-8")) > _MAX_DM_SIZE:
        raise HTTPException(status_code=413, detail="Message content exceeds maximum size")

    tid = _tid(auth)
    backends = request.app.state.backends

    # Calculate body fingerprint for idempotency verification
    body_fp = _body_fingerprint(msg.model_dump()) if idempotency_key else None
    idempotency_cache_key = f"dm:{idempotency_key}" if idempotency_key else None

    # Atomically reserve the idempotency key (prevents race conditions)
    # Returns 409 if same key used with different body
    if idempotency_cache_key:
        cached = await backends.idempotency.reserve(
            tid, idempotency_cache_key, body_fp, _IDEMPOTENCY_TTL
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
    # If this fails, log warning but still return result — the message was sent
    if idempotency_cache_key:
        try:
            await backends.idempotency.set(
                tid, idempotency_cache_key, body_fp, result, _IDEMPOTENCY_TTL
            )
        except Exception as e:
            _logger.warning("Failed to store idempotency result: %s", e)

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
    """List all known participants in the tenant.

    Authorization:
    - Legacy auth: returns all participants (backward compatibility)
    - JWT admin: returns all participants
    - JWT user: returns empty list (use room membership to discover participants)

    This prevents regular users from enumerating all participants in a tenant.
    """
    # Only admins and legacy auth can list all participants
    if auth.is_legacy or auth.role == "admin":
        return await request.app.state.backends.participants.list_all(_tid(auth))
    # Regular users get empty list - use room membership instead
    return []
