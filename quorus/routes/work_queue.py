"""Work-queue routes — Stream B.

Surfaces the durable counterpart to the Stream A social-verb claim graph:

* ``GET /v1/work_queue/{room_id}`` — list active tasks for a room.
* ``POST /v1/work_queue/{room_id}`` — register, claim, update, complete,
  or release a task. The single endpoint dispatches on the body's
  ``op`` field so we keep the route surface narrow.

State is owned by :class:`quorus.services.work_queue_svc.WorkQueueSvc` and
attached lazily to ``app.state.work_queue_service`` on first use, mirroring
the Stream A pattern in ``quorus/routes/social.py``. Reset is wired into
``relay_routes.reset_state`` via :func:`_init_services` so tests start clean.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.auth.policy import (
    Decision,
    PolicyContext,
    evaluate_scoped,
    load_policy_for_tenant,
)
from quorus.routes.room_auth import require_room_member
from quorus.services.work_queue_svc import (
    ClaimRaceError,
    QuotaExceededError,
    TaskNotFoundError,
    WorkQueueError,
    WorkQueueSvc,
)

router = APIRouter()
_LEGACY_TENANT = "_legacy"


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def _work_queue_svc(request: Request) -> WorkQueueSvc:
    """Lazy-attach a :class:`WorkQueueSvc` to ``app.state``.

    Mirrors ``_social_svc`` in ``routes/social.py`` so ``reset_state()`` can
    drop the service and re-create it without touching the queue surface.
    """
    svc = getattr(request.app.state, "work_queue_service", None)
    if svc is None:
        svc = WorkQueueSvc()
        request.app.state.work_queue_service = svc
    return svc


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


WorkQueueOp = Literal[
    "add", "claim", "update", "complete", "release",
]


class WorkQueueRequest(BaseModel):
    """One body shape per endpoint — dispatched server-side on ``op``.

    Keeping the verbs in a single body keeps the public route surface
    narrow (``POST /v1/work_queue/{room_id}``) and matches how the Stream A
    spec exposes social verbs (``POST /v1/social/{verb}``). The route only
    needs to know membership + auth; per-op semantics live in the service.
    """

    model_config = {"extra": "forbid"}

    op: WorkQueueOp
    actor: str | None = Field(default=None, max_length=200)
    task_id: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=500)
    requested_by: str | None = Field(default=None, max_length=200)
    eta_seconds: int | None = Field(default=None, ge=0, le=86_400)
    status: str | None = Field(default=None, max_length=40)
    outcome: str | None = Field(default=None, max_length=500)
    success: bool = True
    handoff_to: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] | None = None

    @field_validator("metadata")
    @classmethod
    def _bound_metadata(cls, v: dict[str, Any] | None):
        if v is None:
            return v
        if len(v) > 16:
            raise ValueError("metadata supports at most 16 keys")
        return v


# ---------------------------------------------------------------------------
# GET — list
# ---------------------------------------------------------------------------


@router.get("/v1/work_queue/{room_id}")
async def list_work_queue(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    status: str | None = None,
    actor: str | None = None,
    limit: int = 100,
):
    """Return the active task list for a room.

    Query params:
      * ``status`` — comma-separated list of statuses to include.
      * ``actor``  — filter to tasks claimed by this actor.
      * ``limit``  — capped at 500 by the service.
    """
    tid = _tid(auth)
    rid, _room = await require_room_member(request, auth, tid, room_id)
    svc = _work_queue_svc(request)

    statuses: list[str] | None = None
    if status:
        # Accept comma-separated list; service normalises each entry.
        statuses = [s.strip() for s in status.split(",") if s.strip()]

    try:
        tasks = await svc.list(
            tid, rid,
            status=statuses, actor=actor or None, limit=limit,
        )
    except WorkQueueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"room_id": rid, "tasks": tasks, "count": len(tasks)}


# ---------------------------------------------------------------------------
# POST — dispatch on op
# ---------------------------------------------------------------------------


@router.post("/v1/work_queue/{room_id}")
async def post_work_queue(
    room_id: str,
    body: WorkQueueRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    # H6 fix — reject legacy bearer entirely on this endpoint.
    if auth.is_legacy:
        raise HTTPException(
            status_code=403,
            detail="legacy auth not permitted on this endpoint",
        )
    if not auth.sub:
        raise HTTPException(status_code=401, detail="auth.sub required")

    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, room_id)

    actor = (body.actor or auth.sub or "").strip()
    if not actor:
        raise HTTPException(status_code=400, detail="actor required")
    if actor != auth.sub:
        raise HTTPException(
            status_code=403, detail="cannot mutate as another actor",
        )

    # Cedar policy gate — same pattern as routes/social.py. Reuses the
    # social membership check (the room_members extra) so any non-member
    # who slipped past require_room_member still gets denied at the policy
    # layer when in HARD mode. We map work-queue ops to the closest social
    # verb so a single policy file can express
    # "agent X may claim but not release".
    policy_dir = getattr(request.app.state, "policy_dir", None)
    policy = load_policy_for_tenant(tid, policy_dir=policy_dir)
    raw_members = room_data.get("members") or {}
    if isinstance(raw_members, dict):
        members = list(raw_members.keys())
    else:
        members = list(raw_members)
    # Map work-queue op → Cedar action. We piggyback on the social
    # vocabulary so a single policy controls both surfaces.
    op_to_action = {
        "add": "social:queue",
        "claim": "social:claim",
        "update": "social:claim",
        "complete": "social:release",
        "release": "social:release",
    }
    action = op_to_action.get(body.op, f"work_queue:{body.op}")
    ctx = PolicyContext(
        actor=actor,
        action=action,
        resource=rid,
        role=auth.role or "agent",
        tenant_id=tid,
        extra={"room_members": members},
    )
    result = evaluate_scoped(ctx, policy)
    if result.effective_decision != Decision.ALLOW:
        raise HTTPException(
            status_code=403,
            detail=f"work_queue policy: {result.reason}",
        )

    # Rate-limit per actor — same shape as Stream A's social rate limit.
    rate_limit_svc = request.app.state.rate_limit_service
    if not await rate_limit_svc.check_with_limit(
        tid, f"work_queue:{actor}", 60,
    ):
        raise HTTPException(429, detail="work-queue rate limit (60/min)")

    svc = _work_queue_svc(request)

    try:
        if body.op == "add":
            if not body.summary:
                raise HTTPException(
                    status_code=400, detail="summary required for op=add",
                )
            task = await svc.add(
                tid, rid,
                summary=body.summary,
                requested_by=body.requested_by or actor,
                eta_seconds=int(body.eta_seconds or 0),
                task_id=body.task_id,
                metadata=body.metadata,
            )
            return {"ok": True, "task": task}

        if body.op == "claim":
            if not body.task_id:
                raise HTTPException(
                    status_code=400, detail="task_id required for op=claim",
                )
            task = await svc.claim(
                tid, rid,
                task_id=body.task_id, actor=actor,
                eta_seconds=body.eta_seconds,
            )
            return {"ok": True, "task": task}

        if body.op == "update":
            if not body.task_id:
                raise HTTPException(
                    status_code=400, detail="task_id required for op=update",
                )
            task = await svc.update(
                tid, rid,
                task_id=body.task_id, actor=actor,
                status=body.status,
                eta_seconds=body.eta_seconds,
                metadata_patch=body.metadata,
            )
            return {"ok": True, "task": task}

        if body.op == "complete":
            if not body.task_id:
                raise HTTPException(
                    status_code=400, detail="task_id required for op=complete",
                )
            task = await svc.complete(
                tid, rid,
                task_id=body.task_id, actor=actor,
                outcome=body.outcome or ("ok" if body.success else "failed"),
                success=body.success,
            )
            return {"ok": True, "task": task}

        if body.op == "release":
            if not body.task_id:
                raise HTTPException(
                    status_code=400, detail="task_id required for op=release",
                )
            task = await svc.release(
                tid, rid,
                task_id=body.task_id, actor=actor,
                handoff_to=body.handoff_to,
            )
            return {"ok": True, "task": task}

    except QuotaExceededError as e:
        # M13 — per-actor active-task quota exceeded. Mirrors the
        # backpressure shape of the rate limiter (429) so clients see one
        # consistent retry signal.
        raise HTTPException(status_code=429, detail=str(e)) from e
    except ClaimRaceError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except WorkQueueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Unreachable: ``WorkQueueRequest.op`` is a closed Literal of the five
    # branches handled above, so Pydantic rejects unknown ops at the
    # validation layer before we ever reach this point. Keeping the type
    # checker honest with an explicit unreachable assertion rather than a
    # dead ``raise HTTPException`` (L28).
    raise AssertionError(f"unreachable: unknown op {body.op!r}")  # pragma: no cover


__all__ = ["router"]
