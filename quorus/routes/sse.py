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

from quorus.auth.middleware import ALLOW_LEGACY_AUTH, AuthContext, verify_auth

router = APIRouter()
logger = structlog.get_logger("quorus.routes.sse")
_LEGACY_TENANT = "_legacy"
RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
SSE_TOKEN_TTL = int(os.environ.get("SSE_TOKEN_TTL", "300"))


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


_SSE_COOKIE_NAME = "quorus_sse"


@router.post("/stream/token")
async def create_sse_token(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    from fastapi.responses import JSONResponse

    body = await request.json()
    recipient = body.get("recipient", "")
    if not recipient:
        raise HTTPException(status_code=400, detail="recipient is required")
    # Bind SSE tokens to the requesting identity. Under JWT: recipient MUST
    # match auth.sub. Under legacy (RELAY_SECRET as admin), only explicit admin
    # role can mint tokens for other recipients — prevents cross-user stream
    # hijack when ALLOW_LEGACY_AUTH is on.
    if auth.sub:
        if recipient != auth.sub:
            raise HTTPException(
                status_code=403,
                detail="Cannot create SSE token for another user",
            )
    elif auth.is_legacy and auth.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Legacy auth without admin cannot mint SSE tokens",
        )
    svc = request.app.state.sse_service
    token = await svc.create_token(_tid(auth), recipient, SSE_TOKEN_TTL)

    # 2026-05-16 hardening: set the SSE token as an HttpOnly, Secure, SameSite
    # cookie scoped to /stream so the browser auto-attaches it on the
    # EventSource request. Eliminates the audit's `?token=` URL exposure
    # (Fly access logs / Referer / browser history). The cookie path matches
    # the SSE endpoint family; the cookie is scoped to a single recipient
    # by virtue of token-recipient binding inside the SSE service.
    resp = JSONResponse({"token": token, "expires_in": SSE_TOKEN_TTL})
    resp.set_cookie(
        key=_SSE_COOKIE_NAME,
        value=token,
        max_age=SSE_TOKEN_TTL,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/stream",
    )
    return resp


@router.get("/stream/{recipient}")
async def stream_messages(
    recipient: str,
    request: Request,
    token: str = "",
):
    svc = request.app.state.sse_service
    # 2026-05-16 hardening: prefer the HttpOnly cookie minted by
    # POST /stream/token. The query-string ``token`` is kept as a
    # backward-compat fallback for CLI/SDK clients that don't run a cookie
    # jar; new browser flows must use the cookie path (see dashboard.js).
    if not token:
        token = request.cookies.get(_SSE_COOKIE_NAME, "")
    valid, tid = await svc.verify_token(token, recipient)
    # Wave-5 Fix 6 — tighten the legacy bypass.
    #
    # Allow RELAY_SECRET as a fallback ONLY when:
    #   * legacy auth is on (ALLOW_LEGACY_AUTH), AND
    #   * the deployment is NOT in production mode (DATABASE_URL unset).
    #
    # Production deployments always have DATABASE_URL set, so the legacy
    # bypass is short-circuited there regardless of any operator
    # misconfiguration of ALLOW_LEGACY_AUTH. Without this gate, a compro-
    # mised RELAY_SECRET let any caller subscribe to ANY recipient as
    # tid="_legacy", crossing tenant boundaries.
    in_production = bool(os.environ.get("DATABASE_URL", ""))
    legacy_ok = (
        ALLOW_LEGACY_AUTH
        and not in_production
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
