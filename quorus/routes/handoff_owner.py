"""A stopped handoff receiver's owner can resolve one exact discarded claim."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.auth.policy import Decision, PolicyContext, evaluate_scoped, load_policy_for_tenant
from quorus.routes.room_auth import require_room_writer

router = APIRouter()


class DiscardBody(BaseModel):
    model_config = {"extra": "forbid"}
    room_id: str = Field(min_length=1, max_length=200)
    receiver: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    resolution_id: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/v1/handoff/discard")
async def owner_discard(body: DiscardBody, request: Request,
                        auth: AuthContext = Depends(verify_auth)):
    from quorus.routes.social import _social_svc, _tid
    from quorus.routes.work_queue import _work_queue_svc
    from quorus.services.handoff_discard import discard

    tid = _tid(auth)
    rid, room = await require_room_writer(request, auth, tid, body.room_id)
    permitted = auth.is_legacy or auth.role == "admin" or auth.sub == body.receiver
    if not permitted and auth.sub and auth.claims.get("pk_claim"):
        store = getattr(request.app.state, "participant_keys", None)
        info = await asyncio.to_thread(store.info, body.receiver) if store else None
        permitted = bool(info and info.get("kind") == "agent"
                         and info.get("owner") == auth.sub
                         and info.get("owner_claim_id") == auth.claims["pk_claim"])
    if not permitted:
        raise HTTPException(403, "only the receiver, its verified owner or an admin may discard")
    policy = load_policy_for_tenant(tid, policy_dir=getattr(request.app.state, "policy_dir", None))
    decision = evaluate_scoped(PolicyContext(
        actor=auth.sub or "relay-admin", action="social:release", resource=rid,
        role=auth.role or "agent", tenant_id=tid,
        extra={"room_members": list(room.get("members") or {})}), policy)
    if decision.effective_decision != Decision.ALLOW:
        raise HTTPException(403, "handoff discard policy refused")
    if not await request.app.state.rate_limit_service.check_with_limit(
            tid, f"handoff_discard:{auth.sub or 'admin'}", 30):
        raise HTTPException(429, "handoff discard rate limit")
    result = await discard(_work_queue_svc(request), _social_svc(request), tid, rid,
        receiver=body.receiver, task_id=body.task_id, resolution_id=body.resolution_id)
    return result
