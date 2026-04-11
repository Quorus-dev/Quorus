"""SSE (Server-Sent Events) route handlers — token creation and streaming."""

from __future__ import annotations

import asyncio
import hmac as hmac_mod
import json
import os
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from murmur.auth.middleware import ALLOW_LEGACY_AUTH, AuthContext, verify_auth

router = APIRouter()
logger = structlog.get_logger("murmur.routes.sse")
_LEGACY_TENANT = "_legacy"
RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
SSE_TOKEN_TTL = int(os.environ.get("SSE_TOKEN_TTL", "300"))


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.post("/stream/token")
async def create_sse_token(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    body = await request.json()
    recipient = body.get("recipient", "")
    if not recipient:
        raise HTTPException(status_code=400, detail="recipient is required")
    if auth.sub and recipient != auth.sub:
        raise HTTPException(
            status_code=403, detail="Cannot create SSE token for another user",
        )
    svc = request.app.state.sse_service
    token = await svc.create_token(_tid(auth), recipient, SSE_TOKEN_TTL)
    return {"token": token, "expires_in": SSE_TOKEN_TTL}


@router.get("/stream/{recipient}")
async def stream_messages(
    recipient: str,
    request: Request,
    token: str = "",
):
    svc = request.app.state.sse_service
    valid, tid = await svc.verify_token(token, recipient)
    # Allow RELAY_SECRET as fallback only when legacy auth is enabled
    legacy_ok = (
        ALLOW_LEGACY_AUTH
        and RELAY_SECRET
        and hmac_mod.compare_digest(token, RELAY_SECRET)
    )
    if not (valid or legacy_ok):
        raise HTTPException(status_code=401, detail="Invalid token")
    if not valid:
        tid = _LEGACY_TENANT

    q = svc.register_queue(tid, recipient)
    logger.info("SSE client connected for %s", recipient)

    async def event_generator():
        try:
            connected = json.dumps({
                "participant": recipient,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            yield f"event: connected\ndata: {connected}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"event: message\ndata: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            svc.unregister_queue(tid, recipient, q)
            logger.info("SSE client disconnected for %s", recipient)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
