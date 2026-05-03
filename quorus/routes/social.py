"""Quorus Social Protocol v1 — relay routes.

Two endpoints:

* ``POST /v1/social/{verb}`` — submit a social verb to a room. The body is
  validated against the per-verb payload schema, the state-machine in
  :mod:`quorus.services.social_svc` is updated, and the envelope is
  broadcast to all room members via SSE.
* ``GET /v1/social/state/{room_id}`` — return the social-state matrix
  (claims, blocks, defer-graph, votes, queue, social_credit).

State is in-memory only in Stream A; Stream B owns persistence.
"""
from __future__ import annotations

import json
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.auth.policy import (
    Decision,
    PolicyContext,
    evaluate_scoped,
    load_policy_for_tenant,
)
from quorus.protocol import VERBS, SocialVerb
from quorus.routes.room_auth import require_room_member
from quorus.services.social_svc import (
    BlockedError,
    ClaimRaceError,
    CycleError,
    DoubleVoteError,
    SocialProtocolError,
    SocialSvc,
)

router = APIRouter()
_LEGACY_TENANT = "_legacy"


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def _social_svc(request: Request) -> SocialSvc:
    """Lazy-attach a SocialSvc on app.state. The reset path also clears it
    by overwriting `app.state.social_service`."""
    svc = getattr(request.app.state, "social_service", None)
    if svc is None:
        svc = SocialSvc()
        request.app.state.social_service = svc
    return svc


@router.post("/v1/social/{verb}")
async def post_social_verb(
    verb: str,
    body: dict[str, Any],
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Submit a social verb to a room. See ``docs/SOCIAL_PROTOCOL_v1.md``."""
    if verb not in VERBS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown verb {verb!r}; must be one of: "
                f"{','.join(VERBS)}"
            ),
        )

    body = dict(body or {})
    actor = (auth.sub or body.get("actor") or "").strip()
    if not actor:
        raise HTTPException(status_code=400, detail="actor required")
    if auth.sub and body.get("actor") and body["actor"] != auth.sub:
        raise HTTPException(
            status_code=403, detail="cannot post as another actor",
        )

    room_ref = body.get("room_id") or body.get("room")
    if not room_ref:
        raise HTTPException(status_code=400, detail="room_id required")

    body["kind"] = "social"
    body["verb"] = verb
    body["actor"] = actor
    body.setdefault("room_id", room_ref)

    try:
        sv = SocialVerb.model_validate(body)
    except Exception as e:  # ValidationError or ValueError
        raise HTTPException(
            status_code=400, detail=f"invalid payload: {e}",
        ) from e

    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, room_ref)

    payload_dict = (
        sv.payload if isinstance(sv.payload, dict)
        else sv.payload.model_dump()
    )

    # Cedar policy gate — runs AFTER membership and BEFORE the rate
    # limit / state-machine mutation so a denied actor never gets
    # a counter increment. Legacy auth keeps the historical
    # single-tenant flow open. ``room_members`` is normalised to a
    # list-of-names regardless of whether the backend returns a dict
    # or a list (RoomService can shape it either way under tests).
    if not auth.is_legacy:
        policy_dir = getattr(request.app.state, "policy_dir", None)
        policy = load_policy_for_tenant(tid, policy_dir=policy_dir)
        raw_members = room_data.get("members") or {}
        if isinstance(raw_members, dict):
            members = list(raw_members.keys())
        else:
            members = list(raw_members)
        ctx = PolicyContext(
            actor=actor,
            action=f"social:{verb}",
            resource=rid,
            role=auth.role or ("human" if auth.sub else "agent"),
            tenant_id=tid,
            extra={
                "room_members": members,
                "mode": payload_dict.get("mode"),
            },
        )
        result = evaluate_scoped(ctx, policy)
        if result.effective_decision != Decision.ALLOW:
            raise HTTPException(
                status_code=403,
                detail=f"social policy: {result.reason}",
            )

    rate_limit_svc = request.app.state.rate_limit_service
    is_blocking_disagree = (
        verb == "disagree"
        and payload_dict.get("mode", "advisory") == "blocking"
    )
    if is_blocking_disagree:
        # Hard cap: 1 blocking-disagree per actor per 5 minutes. Mirrors
        # the Cedar policy ``_DISAGREE_BLOCKING_COOLDOWN_S`` so route
        # and policy agree on the same intent — blocking is production
        # safety, not a style verb. The Cedar gate (CRIT-3) is the
        # primary enforcement; this is the route-layer fallback.
        if not await rate_limit_svc.check_with_limit(
            tid,
            f"social_disagree_blocking:{actor}",
            1,
            window=300,
        ):
            raise HTTPException(
                429, detail="blocking-disagree rate limit (1/5min)",
            )
    else:
        if not await rate_limit_svc.check_with_limit(
            tid, f"social:{actor}", 60,
        ):
            raise HTTPException(429, detail="social verb rate limit")

    svc = _social_svc(request)
    try:
        result = await svc.apply(tid, rid, sv)
    except BlockedError as e:
        raise HTTPException(
            status_code=409, detail=f"room_blocked: {e}",
        ) from e
    except ClaimRaceError as e:
        raise HTTPException(
            status_code=409, detail=f"claim_race: {e}",
        ) from e
    except CycleError as e:
        raise HTTPException(
            status_code=400, detail=f"defer_cycle: {e}",
        ) from e
    except DoubleVoteError as e:
        raise HTTPException(
            status_code=400, detail=f"double_vote: {e}",
        ) from e
    except SocialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Stream B mirror — propagate claim/release/queue into the work-queue.
    # Best-effort: a queue mutation failure must NOT roll back the verb,
    # since the social-state matrix is the authoritative truth and the
    # queue is only the TUI-friendly view of it.
    work_queue = getattr(request.app.state, "work_queue_service", None)
    if work_queue is not None and verb in {"claim", "release", "queue"}:
        try:
            await work_queue.apply_social_event(
                tid, rid, verb=verb, actor=actor, payload=payload_dict,
            )
        except Exception:
            # Never break the verb post on a queue mirror failure.
            pass

    # SSE broadcast — all members get the typed envelope as a "social"
    # message so reflexd / TUI can detect and route it.
    sse_svc = request.app.state.sse_service
    room_name = room_data.get("name", rid)
    members = room_data.get("members", {}) or {}
    envelope = sv.to_envelope()
    for recipient in members:
        sse_svc.push(tid, recipient, {
            "id": str(_uuid.uuid4()),
            "from_name": actor,
            "to": recipient,
            "room": room_name,
            "content": json.dumps(envelope),
            "message_type": "social",
            "timestamp": sv.ts,
        })

    # Best-effort history mirror so polling clients see the verb too. The
    # send() call may itself rate-limit or insist on membership; both are
    # acceptable here since we already verified above.
    rmsg = getattr(request.app.state, "room_msg_service", None)
    if rmsg is not None:
        try:
            await rmsg.send(
                tid, rid, actor, json.dumps(envelope),
                message_type="social", reply_to=sv.ref_message_id,
            )
        except Exception:
            # SSE-only delivery is acceptable fallback for Stream A.
            pass

    return {
        "ok": True,
        "envelope": envelope,
        "state_change": result,
    }


@router.get("/v1/social/state/{room_id}")
async def get_social_state(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Return the social state matrix for ``room_id``."""
    tid = _tid(auth)
    rid, _ = await require_room_member(request, auth, tid, room_id)
    state = await _social_svc(request).get_state(tid, rid)
    return {
        "room_id": rid,
        "blocked_until_resolved": bool(state.get("blocked_until_resolved")),
        "blocking_disagree_ref": state.get("blocking_disagree_ref"),
        "defer_graph": state.get("defer_graph", {}),
        "votes": state.get("votes", {}),
        "voters": state.get("voters", {}),
        "social_credit": state.get("social_credit", {}),
        "claims": state.get("claims", {}),
        "queue": state.get("queue", []),
    }
