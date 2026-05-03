"""Agent-to-agent direct messages — Stream B.

A separate channel from the human DM inbox so reflexd-driven agent chatter
("here's the artifact you asked for", "I'm done with task-#42") doesn't
pollute a human's notification feed. Three routes:

* ``POST /v1/dm`` — send an agent DM. Body: ``{from, to, content, in_reply_to?}``.
  Recipient sees the message on their dedicated stream
  (``/stream/dm/{participant}``) AND in the durable agent-DM inbox.
* ``GET /v1/dm/{participant}`` — list the agent DMs queued for *participant*.
  Returns the most recent first; tail-pagination via ``before_id``.
* ``GET /stream/dm/{participant}`` — SSE stream gated by an SSE token
  (recipient must be ``auth.sub`` or admin). Distinct from
  ``/stream/{participant}`` so the consumer can subscribe to one or both.

Storage
-------
Per-recipient ring buffer kept on ``app.state.agent_dm_inbox`` (a defaultdict
of deque). Soft-cap at 1000 entries per recipient (oldest evicted) — same
order of magnitude as the room-history cap. We deliberately don't reuse the
human-DM backend so agent traffic doesn't compete with human DM rate limits
or quota.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.auth.policy import (
    Decision,
    PolicyContext,
    evaluate_scoped,
    load_policy_for_tenant,
)
from quorus.routes.helpers import _validate_name

router = APIRouter()

# Agent-DM event marker — distinct from "chat" so subscribers can route
# without collision with the room message broadcast format. Keep this in
# sync with reflexd's DM-stream handler in scripts/reflexd.py.
DM_EVENT_KIND = "agent_dm"

# Soft cap per recipient. Older messages are evicted (deque maxlen) so a
# noisy agent can't grow unbounded. Operators can poll the inbox endpoint
# to drain.
INBOX_MAXLEN = 1000

# Max DM body bytes. Agent DMs are typically status pings; cap small to
# discourage shipping artefacts through DM.
MAX_DM_BYTES = 16 * 1024  # 16KB


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or "_legacy"


def _get_inbox(
    request: Request,
) -> dict[tuple[str, str], Deque[dict[str, Any]]]:
    """Lazily create the in-memory agent-DM inbox on app.state.

    Keyed by ``(tenant_id, recipient)``. Distinct from the human DM
    queue (``backends.messages``) so:
      1. Agent traffic doesn't compete with human DM rate limits.
      2. ``reset_state()`` for tests gets a clean slate without
         touching the human inboxes.
    """
    inbox = getattr(request.app.state, "agent_dm_inbox", None)
    if inbox is None:
        inbox = {}
        request.app.state.agent_dm_inbox = inbox
    return inbox


def _get_dm_queues(
    request: Request,
) -> dict[tuple[str, str], list[asyncio.Queue]]:
    """Per-(tenant, participant) list of subscriber queues for the agent
    DM SSE stream. Mirrors :class:`SSEService._queues` but kept distinct
    so subscriptions don't collide.
    """
    queues = getattr(request.app.state, "agent_dm_queues", None)
    if queues is None:
        queues = {}
        request.app.state.agent_dm_queues = queues
    return queues


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class AgentDMRequest(BaseModel):
    """Body for ``POST /v1/dm``.

    The relay derives the *kind* (``agent_dm``) and ``ts`` server-side; we
    accept ``in_reply_to`` so DM threads can be reconstructed without
    leaking thread-ids back into the room message store.
    """

    model_config = {"extra": "forbid"}

    from_name: str = Field(min_length=1, max_length=200, alias="from")
    to: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=MAX_DM_BYTES)
    in_reply_to: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] | None = None

    @field_validator("from_name", "to")
    @classmethod
    def _validate_names(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("metadata")
    @classmethod
    def _bound_metadata(cls, v: dict[str, Any] | None):
        if v is None:
            return v
        if len(v) > 16:
            raise ValueError("metadata supports at most 16 keys")
        return v


# ---------------------------------------------------------------------------
# POST — send
# ---------------------------------------------------------------------------


@router.post("/v1/dm")
async def send_agent_dm(
    body: AgentDMRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Send an agent DM. Distinct from the human DM stream."""
    sender = body.from_name
    recipient = body.to

    # Identity binding — the JWT sub must match the from field unless
    # legacy admin auth is in play (auth.sub absent and role=admin).
    if auth.sub and sender != auth.sub:
        raise HTTPException(
            status_code=403, detail="cannot send DM as another user",
        )

    # Self-DM is meaningless and almost certainly a misconfig (e.g. a
    # reflexd loop pointed at its own participant). Reject loudly.
    if sender == recipient:
        raise HTTPException(
            status_code=400, detail="cannot DM yourself",
        )

    if len(body.content.encode("utf-8")) > MAX_DM_BYTES:
        raise HTTPException(
            status_code=413, detail="DM content exceeds 16KB cap",
        )

    tid = _tid(auth)

    # Rate limit: per (sender, recipient) pair so a noisy agent can't
    # flood one peer while still being able to fan out to others.
    rate_limit_svc = request.app.state.rate_limit_service
    if not await rate_limit_svc.check_with_limit(
        tid, f"agent_dm:{sender}:{recipient}", 30,
    ):
        raise HTTPException(429, detail="agent-DM rate limit (30/min)")

    # Cross-tenant + recipient-existence guard. Recipient MUST be a
    # registered participant in the sender's tenant. Participants are
    # partitioned per-tenant so this naturally rejects any attempt to
    # DM a name that only lives in a different tenant. Legacy auth
    # (no real JWT) skips the lookup so existing single-tenant flows
    # keep working — same exemption shape as require_room_member.
    if not auth.is_legacy:
        backends = getattr(request.app.state, "backends", None)
        participants = (
            getattr(backends, "participants", None) if backends else None
        )
        if participants is not None:
            try:
                known = await participants.list_all(tid)
            except Exception:
                known = []
            if recipient not in known:
                raise HTTPException(
                    status_code=404,
                    detail="recipient not found in tenant",
                )

        # Cedar policy gate — same shape as social/work_queue. The DM
        # scoped check ensures sender_tenant == recipient_tenant; we
        # pass tid as both because the membership lookup above already
        # enforced co-tenancy, so any name that survived the lookup is
        # by definition in the sender's tenant.
        policy_dir = getattr(request.app.state, "policy_dir", None)
        policy = load_policy_for_tenant(tid, policy_dir=policy_dir)
        ctx = PolicyContext(
            actor=sender,
            action="dm:agent",
            resource=recipient,
            role=auth.role or ("human" if auth.sub else "agent"),
            tenant_id=tid,
            extra={"recipient_tenant_id": tid},
        )
        result = evaluate_scoped(ctx, policy)
        if result.effective_decision != Decision.ALLOW:
            raise HTTPException(
                status_code=403,
                detail=f"dm policy: {result.reason}",
            )

    # Build the envelope. Mirrors the social-verb envelope shape so
    # reflexd's DM handler can route on `kind` without forking.
    now_iso = datetime.now(timezone.utc).isoformat()
    message_id = str(uuid.uuid4())
    envelope = {
        "id": message_id,
        "kind": DM_EVENT_KIND,
        "from": sender,
        "from_name": sender,
        "to": recipient,
        "content": body.content,
        "in_reply_to": body.in_reply_to,
        "timestamp": now_iso,
        "ts": now_iso,
        "metadata": dict(body.metadata or {}),
    }

    # Persist to the recipient inbox (ring buffer eviction).
    inbox = _get_inbox(request)
    key = (tid, recipient)
    queue = inbox.get(key)
    if queue is None:
        queue = deque(maxlen=INBOX_MAXLEN)
        inbox[key] = queue
    queue.append(envelope)

    # Push to any active SSE subscribers. We deliberately skip
    # ``backends.messages`` so this DM never appears in the human inbox.
    queues = _get_dm_queues(request)
    for q in queues.get(key, []):
        try:
            q.put_nowait(envelope)
        except asyncio.QueueFull:
            # Slow consumer — drop oldest queued item to make room.
            try:
                _ = q.get_nowait()
                q.put_nowait(envelope)
            except Exception:
                pass

    return {"ok": True, "id": message_id, "timestamp": now_iso}


# ---------------------------------------------------------------------------
# GET — inbox
# ---------------------------------------------------------------------------


@router.get("/v1/dm/{participant}")
async def list_agent_dms(
    participant: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    limit: int = 50,
    before_id: str | None = None,
):
    """List queued agent DMs for *participant*. Newest last (chrono)."""
    if auth.sub and auth.sub != participant:
        # Only the recipient (or admin via legacy auth) can read their
        # own agent DM inbox.
        if not (auth.is_legacy and auth.role == "admin"):
            raise HTTPException(
                status_code=403, detail="cannot read another user's DMs",
            )

    tid = _tid(auth)
    inbox = _get_inbox(request)
    queue = inbox.get((tid, participant)) or deque(maxlen=INBOX_MAXLEN)
    items = list(queue)
    if before_id:
        cut = next(
            (i for i, m in enumerate(items) if m.get("id") == before_id),
            len(items),
        )
        items = items[:cut]
    limit = min(max(1, limit), 200)
    return {
        "participant": participant,
        "messages": items[-limit:],
        "count": len(items[-limit:]),
        "total_inbox": len(queue),
    }


# ---------------------------------------------------------------------------
# GET — SSE stream
# ---------------------------------------------------------------------------


@router.get("/stream/dm/{participant}")
async def stream_agent_dms(
    participant: str,
    request: Request,
    token: str = "",
):
    """Subscribe to the agent-DM SSE stream for *participant*.

    Token validation reuses :class:`SSEService` so an existing
    ``POST /stream/token`` round-trip is sufficient — clients don't have to
    mint a second token type.
    """
    sse_svc = request.app.state.sse_service
    valid, tid = await sse_svc.verify_token(token, participant)
    if not valid:
        # Optional legacy fallback — kept symmetric with /stream/{recipient}.
        import hmac as _hmac
        import os as _os

        from quorus.auth.middleware import ALLOW_LEGACY_AUTH
        legacy_secret = _os.environ.get("RELAY_SECRET", "")
        if not (
            ALLOW_LEGACY_AUTH
            and legacy_secret
            and _hmac.compare_digest(token, legacy_secret)
        ):
            raise HTTPException(status_code=401, detail="Invalid token")
        tid = "_legacy"

    queues = _get_dm_queues(request)
    key = (tid, participant)
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    queues.setdefault(key, []).append(q)

    async def _gen():
        try:
            connected = json.dumps({
                "kind": "dm_connected",
                "participant": participant,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            yield f"event: connected\ndata: {connected}\n\n"
            # Replay any queued inbox entries the recipient missed
                # while disconnected — bounded by INBOX_MAXLEN. We don't
                # mutate the inbox here (the inbox is the durable copy).
            inbox = _get_inbox(request)
            for env in list(inbox.get(key, [])):
                yield f"event: agent_dm\ndata: {json.dumps(env)}\n\n"
            while True:
                try:
                    envelope = await asyncio.wait_for(q.get(), timeout=30)
                    yield (
                        f"event: agent_dm\ndata: {json.dumps(envelope)}\n\n"
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            try:
                queues[key].remove(q)
            except (KeyError, ValueError):
                pass

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router", "DM_EVENT_KIND"]


# Keep `time` referenced even if the module gets refactored — used by tests
# patching the wall clock in the future.
_ = time
