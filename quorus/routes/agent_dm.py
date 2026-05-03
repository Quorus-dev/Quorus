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
from collections import OrderedDict, deque
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

# M5 fix — global cap on the number of distinct recipient inboxes per
# tenant slice held in memory. Without this, an attacker with a compromised
# parent key can use register-agent + DM-spam to mint 100K recipient names
# and push the relay's RSS into OOM. We use an OrderedDict so eviction is
# strict-LRU (oldest-touched out first). The number is generous enough that
# real swarms (typical < 1k participants) never see eviction; only the
# adversarial path triggers it.
MAX_INBOXES = 10_000

# Max DM body bytes. Agent DMs are typically status pings; cap small to
# discourage shipping artefacts through DM.
MAX_DM_BYTES = 16 * 1024  # 16KB


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or "_legacy"


def _get_inbox(
    request: Request,
) -> "OrderedDict[tuple[str, str], Deque[dict[str, Any]]]":
    """Lazily create the in-memory agent-DM inbox on app.state.

    Keyed by ``(tenant_id, recipient)``. Distinct from the human DM
    queue (``backends.messages``) so:
      1. Agent traffic doesn't compete with human DM rate limits.
      2. ``reset_state()`` for tests gets a clean slate without
         touching the human inboxes.

    M5 fix: backed by an OrderedDict so callers can perform strict-LRU
    eviction via :func:`_inbox_get_or_create`. Existing dict deployments
    (pre-fix relays) gracefully migrate at first access — the legacy
    plain dict gets re-wrapped on the first call.
    """
    inbox = getattr(request.app.state, "agent_dm_inbox", None)
    if inbox is None:
        inbox = OrderedDict()
        request.app.state.agent_dm_inbox = inbox
    elif not isinstance(inbox, OrderedDict):
        # Migrate a legacy dict (e.g. relay restarted into mixed state).
        # Order is undefined here so eviction starts deterministic again.
        inbox = OrderedDict(inbox)
        request.app.state.agent_dm_inbox = inbox
    return inbox


def _inbox_get_or_create(
    inbox: "OrderedDict[tuple[str, str], Deque[dict[str, Any]]]",
    key: tuple[str, str],
) -> Deque[dict[str, Any]]:
    """Return the deque for *key*, evicting the LRU inbox if MAX_INBOXES
    would be exceeded. Promotes existing entries to the right end.

    The eviction policy is strict-LRU: the leftmost (oldest-touched)
    inbox loses its queue when capacity is hit. That deque's contents
    are dropped — the OrderedDict slot is reused. A subsequent
    ``GET /v1/dm/<evicted>`` returns an empty deque rather than 404
    so the API surface stays uniform; callers that genuinely needed
    durability should subscribe to /stream/dm/{participant}, which is
    bounded by per-subscriber queues that the consumer drains.
    """
    queue = inbox.get(key)
    if queue is not None:
        inbox.move_to_end(key)
        return queue
    if len(inbox) >= MAX_INBOXES:
        inbox.popitem(last=False)
    queue = deque(maxlen=INBOX_MAXLEN)
    inbox[key] = queue
    return queue


def _get_dm_queues(
    request: Request,
) -> dict[tuple[str, str], set[asyncio.Queue]]:
    """Per-(tenant, participant) set of subscriber queues for the agent
    DM SSE stream. Mirrors :class:`SSEService._queues` but kept distinct
    so subscriptions don't collide.

    M17 fix: switched from ``list[asyncio.Queue]`` to ``set`` so
    cleanup-on-disconnect is O(1) instead of O(n). Iteration order on a
    set is undefined, but DM fan-out is order-agnostic — every queue
    receives a copy of the envelope and the order they're dispatched in
    is irrelevant to downstream consumers (each subscriber sees a
    self-consistent ordering of arrival on its own queue). Legacy lists
    on app.state are migrated transparently.
    """
    queues = getattr(request.app.state, "agent_dm_queues", None)
    if queues is None:
        queues = {}
        request.app.state.agent_dm_queues = queues
    elif queues:
        # Migrate any legacy list-of-queues shape from a relay running
        # the pre-M17 code path. Done lazily on first access so the cost
        # is bounded by the number of (tid, participant) keys, not by
        # the request rate.
        for key, val in list(queues.items()):
            if isinstance(val, list):
                queues[key] = set(val)
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

    # H6 fix — reject legacy bearer entirely. Without ``auth.sub`` an
    # attacker can pass arbitrary ``from`` and impersonate another agent.
    if auth.is_legacy:
        raise HTTPException(
            status_code=403,
            detail="legacy auth not permitted on this endpoint",
        )
    if not auth.sub:
        raise HTTPException(status_code=401, detail="auth.sub required")
    if sender != auth.sub:
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
    # DM a name that only lives in a different tenant. Legacy auth was
    # rejected above, so we always run this check now.
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
        role=auth.role or "agent",
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

    # Persist to the recipient inbox (ring buffer eviction). M5 — uses
    # the LRU-bounded helper so a flood of distinct recipients can't OOM
    # the relay; oldest-touched inbox is evicted at MAX_INBOXES.
    inbox = _get_inbox(request)
    key = (tid, recipient)
    queue = _inbox_get_or_create(inbox, key)
    queue.append(envelope)

    # Push to any active SSE subscribers. We deliberately skip
    # ``backends.messages`` so this DM never appears in the human inbox.
    # M17 — `queues[key]` is now a set; iteration is unordered but
    # fan-out is order-agnostic. We snapshot the set into a tuple before
    # iterating so a concurrent stream-handler ``add``/``discard`` (the
    # producer side runs without holding the lock) can't trip
    # ``RuntimeError: Set changed size during iteration``.
    queues = _get_dm_queues(request)
    subscribers = queues.get(key)
    if subscribers:
        for q in tuple(subscribers):
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                # Slow consumer — drop oldest queued item to make room.
                try:
                    _ = q.get_nowait()
                    q.put_nowait(envelope)
                except Exception:
                    pass

    # H1 fix — audit trail for SOC2 + GDPR. Record sender/recipient
    # metadata only, never the DM body content. Best-effort: missing
    # ledger (non-Postgres rigs) is non-fatal.
    audit_svc = getattr(request.app.state, "audit_service", None)
    if audit_svc is not None:
        try:
            try:
                msg_uuid = uuid.UUID(message_id)
            except ValueError:
                msg_uuid = uuid.uuid4()
            await audit_svc.record(
                tenant_id=tid,
                message_id=msg_uuid,
                event_type="dm:agent",
                actor=sender,
                target=recipient,
                details={
                    "in_reply_to": body.in_reply_to,
                    "metadata_keys": (
                        sorted(body.metadata.keys())
                        if isinstance(body.metadata, dict) else []
                    ),
                },
            )
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
# DELETE — purge inbox (GDPR right-to-erasure)
# ---------------------------------------------------------------------------


@router.delete("/v1/dm/{participant}")
async def delete_agent_dms(
    participant: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Erase a participant's agent-DM inbox.

    GDPR Article 17 ("right to erasure") requires self-serve purge.
    Recipient-only — no admin override and no legacy-bearer override,
    because once a delete-on-behalf-of path exists every operator
    becomes a data-erasure attack vector. Auth.sub MUST equal the
    target participant.

    Returns ``{"purged_count": N}`` so callers can prove the operation
    landed (and supplies an audit-trail hook for SOC2).
    """
    # Reject legacy bearer entirely — see H6 reasoning in send_agent_dm.
    if auth.is_legacy:
        raise HTTPException(
            status_code=403,
            detail="legacy auth not permitted on this endpoint",
        )
    if not auth.sub:
        raise HTTPException(status_code=401, detail="auth.sub required")
    if auth.sub != participant:
        # Only the recipient can erase their own data. No admin override:
        # GDPR erasure is a strictly self-driven right and an admin path
        # would let one compromised account wipe peer inboxes.
        raise HTTPException(
            status_code=403,
            detail="cannot purge another user's DMs",
        )

    tid = _tid(auth)
    inbox = _get_inbox(request)
    key = (tid, participant)
    queue = inbox.get(key)
    purged_count = len(queue) if queue is not None else 0

    if queue is not None:
        # Drop the deque entirely (also evicts the OrderedDict slot under
        # the M5 LRU cap so deleted recipients free up the inbox budget).
        inbox.pop(key, None)

    # Best-effort audit record. Body content was never logged; we record
    # only the count so SOC2 / GDPR can prove the erasure happened.
    audit_svc = getattr(request.app.state, "audit_service", None)
    if audit_svc is not None:
        try:
            await audit_svc.record(
                tenant_id=tid,
                message_id=uuid.uuid4(),
                event_type="dm:agent:purge",
                actor=auth.sub,
                target=participant,
                details={"purged_count": purged_count},
            )
        except Exception:
            pass

    return {"purged_count": purged_count, "participant": participant}


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
    # M17 — set-based fan-out: O(1) add and O(1) remove on disconnect.
    queues.setdefault(key, set()).add(q)

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
            # M17 — set.discard is O(1) and silent on a missing element,
            # so we don't need the (KeyError, ValueError) catch the
            # list-based version required.
            subs = queues.get(key)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    # Drop the empty bucket so the queues map doesn't
                    # accumulate idle keys after every disconnect.
                    queues.pop(key, None)

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
