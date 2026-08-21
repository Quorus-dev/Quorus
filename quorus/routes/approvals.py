"""Approval routes — human-in-the-loop tool permissions (WAKE_REBUILD L3).

* ``POST   /v1/approvals``               — agent asks for permission
* ``GET    /v1/approvals/{id}``          — agent polls for the decision
* ``POST   /v1/approvals/{id}/decision`` — human approves or denies
* ``GET    /v1/approvals?room=<r>``      — list what's waiting on a human

Creating a request broadcasts it into the room over SSE and posts a visible
chat line, so the approval shows up wherever the human already is. Only
room members may create or decide, and an agent may not approve its own
request — that would make the gate decorative.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.routes.room_auth import require_room_member
from quorus.services.approval_svc import ApprovalError, ApprovalSvc

router = APIRouter()
_LEGACY_TENANT = "_legacy"


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def _svc(request: Request) -> ApprovalSvc:
    svc = getattr(request.app.state, "approval_service", None)
    if svc is None:
        svc = ApprovalSvc()
        request.app.state.approval_service = svc
    return svc


class CreateApprovalRequest(BaseModel):
    room_id: str = Field(min_length=1, max_length=200)
    agent: str = Field(min_length=1, max_length=100)
    tool_name: str = Field(min_length=1, max_length=100)
    tool_input: object | None = None
    ttl_seconds: int = Field(default=300, ge=10, le=3600)


class DecisionRequest(BaseModel):
    approve: bool
    reason: str = Field(default="", max_length=500)


def _notify_room(
    request: Request, tid: str, room_name: str, rec: dict,
    members: list[str],
) -> None:
    """Broadcast the pending approval to every room member over SSE."""
    sse = getattr(request.app.state, "sse_service", None)
    if sse is None:
        return
    payload = {
        "event": "approval_requested",
        "approval_id": rec["id"],
        "room": room_name,
        "agent": rec["agent"],
        "tool_name": rec["tool_name"],
        "input_preview": rec["input_preview"],
        "expires_at": rec["expires_at"],
    }
    for member in members:
        try:
            sse.push(tid, member, payload)
        except Exception:  # pragma: no cover - broadcast is best-effort
            pass


@router.post("/v1/approvals")
async def create_approval(
    body: CreateApprovalRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Agent-side: ask a human to allow a tool call."""
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, body.room_id)
    if not auth.is_legacy and auth.sub and body.agent != auth.sub:
        raise HTTPException(
            status_code=403, detail="Cannot request approval as another agent",
        )
    svc = _svc(request)
    try:
        rec = await svc.create(
            tid, room=room_data.get("name", rid), agent=body.agent,
            tool_name=body.tool_name, tool_input=body.tool_input,
            ttl_seconds=body.ttl_seconds,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    members = await request.app.state.backends.rooms.get_members(tid, rid)
    _notify_room(
        request, tid, room_data.get("name", rid), rec, sorted(members.keys()),
    )

    # Also drop a human-readable line in the room so the request is visible
    # in any client, not just SSE-aware ones.
    room_svc = getattr(request.app.state, "room_msg_service", None)
    if room_svc is not None:
        try:
            await room_svc.send(
                tid, rid, body.agent,
                (
                    f"🔐 approval needed: `{body.tool_name}` — "
                    f"{rec['input_preview']}\n"
                    f"approve with `quorus approve {rec['id']}` "
                    f"or deny with `quorus deny {rec['id']}`"
                ),
            )
        except Exception:  # pragma: no cover - chat line is best-effort
            pass
    return rec


@router.get("/v1/approvals/{approval_id}")
async def get_approval(
    approval_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    rec = await _svc(request).get(_tid(auth), approval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown approval request")
    return rec


@router.get("/v1/approvals")
async def list_approvals(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    room: str | None = None,
):
    return {"pending": await _svc(request).list_pending(_tid(auth), room=room)}


@router.post("/v1/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    body: DecisionRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Human-side: allow or deny. The requesting agent may not self-approve."""
    tid = _tid(auth)
    svc = _svc(request)
    rec = await svc.get(tid, approval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown approval request")
    if not auth.is_legacy and auth.sub == rec["agent"]:
        raise HTTPException(
            status_code=403, detail="An agent cannot decide its own approval",
        )
    try:
        decided = await svc.decide(
            tid, approval_id, approve=body.approve,
            decided_by=auth.sub or "operator", reason=body.reason,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return decided
