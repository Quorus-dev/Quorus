"""Room message route handlers — send (with fan-out), history, and search."""

from __future__ import annotations

import hashlib
import json
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.routes.models import RoomMessageRequest
from quorus.routes.room_auth import require_room_member

MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))
_IDEMPOTENCY_TTL = int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", "300"))

router = APIRouter()
_LEGACY_TENANT = "_legacy"
_logger = logging.getLogger(__name__)


def _body_fingerprint(body: dict) -> str:
    """Create a short fingerprint of the request body for idempotency binding."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


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
      The key is bound to the request body — different content with the
      same key is treated as a new request.
    """
    sender = auth.sub or msg.from_name
    if auth.sub and msg.from_name != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot send as another user")
    if len(msg.content.encode("utf-8")) > MAX_MESSAGE_SIZE:
        raise HTTPException(status_code=413, detail="Message content exceeds maximum size")

    tid = _tid(auth)

    # Rate limit: 60/min per sender (agents send frequently)
    rate_limit_svc = request.app.state.rate_limit_service
    if not await rate_limit_svc.check_with_limit(tid, f"room_msg:{sender}", 60):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    backends = request.app.state.backends

    # Calculate body fingerprint for idempotency verification
    body_fp = _body_fingerprint(msg.model_dump()) if idempotency_key else None
    idempotency_cache_key = f"room:{room_id}:{idempotency_key}" if idempotency_key else None

    # Atomically reserve the idempotency key (prevents race conditions)
    # Returns 409 if same key used with different body
    if idempotency_cache_key:
        cached = await backends.idempotency.reserve(
            tid, idempotency_cache_key, body_fp, _IDEMPOTENCY_TTL
        )
        if cached:
            return cached

    svc = request.app.state.room_msg_service
    try:
        result = await svc.send(
            tid, room_id, sender, msg.content,
            message_type=msg.message_type, reply_to=msg.reply_to,
        )
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
    await require_room_member(request, auth, tid, room_id)

    # Rate limit: 120/min — history is the TUI's polling path (~2s cadence).
    # 20/min blocked legitimate multi-user UX and caused silent 429 wipes.
    caller = auth.sub or "anon"
    rate_limit_svc = request.app.state.rate_limit_service
    if not await rate_limit_svc.check_with_limit(tid, f"history:{caller}", 120):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

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
    await require_room_member(request, auth, tid, room_id)
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
    await require_room_member(request, auth, tid, room_id)
    svc = request.app.state.room_msg_service
    return await svc.search(
        tid, room_id, q=q, sender=sender,
        message_type=message_type, limit=limit,
    )
