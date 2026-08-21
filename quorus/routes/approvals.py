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
from quorus.services.approval_svc import (
    ApprovalError,
    ApprovalSvc,
    is_agent_name,
)

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
    # Legacy/admin auth carries no participant identity (``sub`` is None),
    # so an operator using the shared secret must name themselves. Ignored
    # when a real participant identity is present.
    decided_by: str = Field(default="", max_length=100)


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
            tid, room=room_data.get("name", rid), room_id=rid,
            agent=body.agent,
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
                    # rec["tool_name"] is the SANITIZED value; body's is
                    # raw and let an agent forge extra lines in this notice.
                    f"🔐 approval needed: `{rec['tool_name']}` — "
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
    # The requesting agent always sees its own record (it polls this to
    # learn the decision); everyone else must be in the room.
    if auth.sub and auth.sub != rec["agent"]:
        if not await _visible(request, auth, [rec]):
            raise HTTPException(
                status_code=404, detail="Unknown approval request",
            )
    return rec


async def _visible(request: Request, auth: AuthContext, records: list[dict]) -> list[dict]:
    """Drop records for rooms the caller is not in.

    Approval previews carry command lines and file contents; tenant-only
    scoping let any participant (every agent included) enumerate every
    room's pending requests — and harvest the ids needed to decide them.
    Operator/admin credentials still see everything by design.
    """
    if auth.is_legacy or auth.role == "admin" or not auth.sub:
        return records
    visible: list[dict] = []
    for rec in records:
        try:
            members = await request.app.state.backends.rooms.get_members(
                _tid(auth), rec.get("room_id") or rec["room"],
            )
        except Exception:
            continue
        if auth.sub in (members or {}):
            visible.append(rec)
    return visible


@router.get("/v1/approvals")
async def list_approvals(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    room: str | None = None,
):
    pending = await _svc(request).list_pending(_tid(auth), room=room)
    return {"pending": await _visible(request, auth, pending)}


@router.post("/v1/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    body: DecisionRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Human-side: allow or deny.

    A gate an agent can open is not a gate, so deciding requires an
    identity that (a) is named, (b) is not an agent, (c) is not the
    requesting agent, and (d) belongs to the room. Previously the endpoint
    checked only ``auth.sub == rec["agent"]`` — and skipped even that under
    legacy auth, which is exactly the credential the MCP server falls back
    to. Any second agent in the tenant could approve the first one's Bash
    call, from a room it had never joined.
    """
    tid = _tid(auth)
    svc = _svc(request)
    rec = await svc.get(tid, approval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown approval request")

    # (a) Who is deciding? Legacy auth has no participant identity, so the
    # caller must name themselves; a bare shared secret decides nothing.
    decider = (auth.sub or body.decided_by or "").strip()
    if not decider:
        raise HTTPException(
            status_code=403,
            detail=(
                "Approvals need a named decider. Use a participant API key, "
                "or pass decided_by with an operator identity."
            ),
        )
    # (b) + (c) Agents never decide — least of all their own request.
    if decider == rec["agent"] or is_agent_name(decider):
        raise HTTPException(
            status_code=403, detail="An agent cannot decide an approval",
        )
    # (d) …and only someone in the room can speak for it.
    members = await request.app.state.backends.rooms.get_members(
        tid, rec.get("room_id") or rec["room"],
    )
    if decider not in (members or {}):
        raise HTTPException(
            status_code=403, detail="Only a room member can decide this",
        )
    try:
        decided = await svc.decide(
            tid, approval_id, approve=body.approve,
            decided_by=decider, reason=body.reason,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return decided
