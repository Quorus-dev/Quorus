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
from pydantic import BaseModel, Field, field_validator

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


class SocialVerbBody(BaseModel):
    """Strict body model for ``POST /v1/social/{verb}``.

    H8 fix: replaces the loose ``body.get("actor")`` chain with Pydantic
    validation so empty/null actor and missing room_id fail fast with a 422
    instead of silently propagating into the state machine.
    """

    model_config = {"extra": "allow"}

    actor: str = Field(min_length=1, max_length=200)
    room_id: str | None = Field(default=None, max_length=200)
    room: str | None = Field(default=None, max_length=200)
    ref_message_id: str | None = Field(default=None, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("actor")
    @classmethod
    def _strip_actor(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("actor required")
        return v


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


def _room_members(room_data: dict[str, Any]) -> list[str]:
    """Normalise ``room_data['members']`` to a list of participant names.

    The RoomService can shape ``members`` as either a list (legacy) or a
    dict keyed by participant name (current). Verb endpoints need a flat
    list for policy + SSE fan-out; this helper consolidates the two
    extraction sites that previously duplicated the isinstance check.
    """
    raw = room_data.get("members") or {}
    if isinstance(raw, dict):
        return list(raw.keys())
    return list(raw)


@router.post("/v1/social/{verb}")
async def post_social_verb(
    verb: str,
    body: SocialVerbBody,
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

    # H6 (code-review) — reject legacy bearer entirely on this endpoint.
    # Legacy auth has ``auth.sub=None``, which would let any caller pass
    # an arbitrary ``actor`` field and impersonate another participant.
    # The route requires a real JWT so the actor is bound to ``auth.sub``.
    if auth.is_legacy:
        raise HTTPException(
            status_code=403,
            detail="legacy auth not permitted on this endpoint",
        )
    if not auth.sub:
        raise HTTPException(status_code=401, detail="auth.sub required")

    actor = body.actor
    # H6 (code-review) — strict actor/JWT binding. Anti-impersonation must
    # not depend on ``auth.sub`` being present.
    if actor != auth.sub:
        raise HTTPException(
            status_code=403,
            detail="cannot post as another actor",
        )

    room_ref = body.room_id or body.room
    if not room_ref:
        raise HTTPException(status_code=400, detail="room_id required")

    raw = body.model_dump()
    raw["kind"] = "social"
    raw["verb"] = verb
    raw["actor"] = actor
    raw.setdefault("room_id", room_ref)
    raw.pop("room", None)

    try:
        sv = SocialVerb.model_validate(raw)
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

    # H7 fix — for blocking-disagree, validate the ref_message_id resolves
    # to a real message in recent room history. Without this an attacker
    # can freeze the room indefinitely by referencing a fabricated id.
    if verb == "disagree" and payload_dict.get("mode") == "blocking":
        ref_id = payload_dict.get("ref_message_id")
        if ref_id:
            backends = getattr(request.app.state, "backends", None)
            history_backend = (
                getattr(backends, "room_history", None)
                if backends else None
            )
            history: list[dict[str, Any]] = []
            if history_backend is not None:
                try:
                    history = await history_backend.get_recent(
                        tid, rid, limit=500,
                    )
                except Exception:
                    history = []
            if not any(
                m.get("id") == ref_id or m.get("message_id") == ref_id
                for m in history
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"ref_message_id {ref_id!r} not found in recent "
                        "room history"
                    ),
                )

    # Cedar policy gate — runs AFTER membership and BEFORE the rate
    # limit / state-machine mutation so a denied actor never gets
    # a counter increment. ``room_members`` is normalised to a
    # list-of-names regardless of whether the backend returns a dict
    # or a list (RoomService can shape it either way under tests).
    policy_dir = getattr(request.app.state, "policy_dir", None)
    policy = load_policy_for_tenant(tid, policy_dir=policy_dir)
    members = _room_members(room_data)
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
        result = await svc.apply(
            tid, rid, sv, room_member_count=len(members),
        )
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
    # Wave-5 Fix 2: mint ONE uuid for both the SSE envelope id and the
    # audit row message_id so SOC2 reviewers can cross-reference them.
    sse_svc = request.app.state.sse_service
    room_name = room_data.get("name", rid)
    members = _room_members(room_data)
    envelope = sv.to_envelope()
    broadcast_id = _uuid.uuid4()
    broadcast_id_str = str(broadcast_id)
    for recipient in members:
        sse_svc.push(tid, recipient, {
            "id": broadcast_id_str,
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

    # H1 fix — audit trail. Record the social verb for SOC2 / GDPR
    # compliance. Best-effort: audit failures must NOT break the verb post.
    # Message content / payload secrets stay out of the ledger; we record
    # the structural fields only (verb, room, actor, ref_message_id, mode).
    audit_svc = getattr(request.app.state, "audit_service", None)
    if audit_svc is not None:
        try:
            audit_details = {
                "verb": verb,
                "room_id": rid,
                "ref_message_id": sv.ref_message_id,
                "broadcast_id": broadcast_id_str,
            }
            # Surface a tiny set of safe payload fields per verb so the
            # audit row has enough context for incident review without
            # leaking content/reason text.
            if verb == "claim":
                audit_details["task_id"] = payload_dict.get("task_id")
                audit_details["eta_seconds"] = payload_dict.get("eta_seconds")
            elif verb == "release":
                audit_details["task_id"] = payload_dict.get("task_id")
                audit_details["handoff_to"] = payload_dict.get("handoff_to")
            elif verb == "disagree":
                audit_details["mode"] = payload_dict.get("mode")
            elif verb == "defer":
                audit_details["to"] = payload_dict.get("to")
                audit_details["ttl_seconds"] = payload_dict.get("ttl_seconds")
            elif verb == "vote":
                audit_details["poll_id"] = payload_dict.get("poll_id")
                audit_details["option"] = payload_dict.get("option")
            elif verb == "queue":
                audit_details["after"] = payload_dict.get("after")
            await audit_svc.record(
                tenant_id=tid,
                message_id=broadcast_id,
                event_type=f"social:{verb}",
                actor=actor,
                target=rid,
                room_id=rid,
                room_name=room_data.get("name"),
                details=audit_details,
            )
        except Exception:
            # Audit ledger may be unavailable (e.g. non-Postgres test rigs).
            # Stream A authoritative truth lives in the in-memory state,
            # so audit gap is non-fatal but logged elsewhere.
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
