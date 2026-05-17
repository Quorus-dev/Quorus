"""Tool catalog routes — Plan v8 Phase 1 OS primitive B.

Three endpoints, all room-scoped:

* ``POST /v1/rooms/{rid}/tools`` — register an MCP server in the room.
  Only room admins (``role=admin`` in the room's member roles) may write;
  the caller must be a room member.
* ``GET /v1/rooms/{rid}/tools`` — list tools registered in the room.
  Members only.
* ``DELETE /v1/rooms/{rid}/tools/{name}`` — remove a tool.
  Room admins only.

Storage is ephemeral per
:class:`quorus.services.tool_catalog_svc.ToolCatalogSvc`.
"""
from __future__ import annotations

import os
import uuid as _uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl, field_validator

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.auth.policy import (
    Decision,
    PolicyContext,
    evaluate_scoped,
    load_policy_for_tenant,
)
from quorus.routes.room_auth import require_room_member
from quorus.services.tool_catalog_svc import (
    DuplicateToolError,
    ToolCatalogError,
    ToolCatalogSvc,
)

router = APIRouter()
logger = structlog.get_logger("quorus.routes.tool_catalog")
_LEGACY_TENANT = "_legacy"


class ToolRegistration(BaseModel):
    """Body for ``POST /v1/rooms/{rid}/tools``."""

    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=100)
    url: HttpUrl
    access: str = Field(default="room", max_length=20)
    description: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name required")
        # Restrict to URL-safe chars so the DELETE path stays sane.
        for ch in v:
            if not (ch.isalnum() or ch in "-_."):
                raise ValueError(
                    "name must be alphanumeric, hyphens, underscores, dots"
                )
        return v


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def _tool_catalog_svc(request: Request) -> ToolCatalogSvc:
    svc = getattr(request.app.state, "tool_catalog_service", None)
    if svc is None:
        svc = ToolCatalogSvc()
        request.app.state.tool_catalog_service = svc
    return svc


def _require_jwt(auth: AuthContext) -> None:
    if auth.is_legacy:
        raise HTTPException(
            status_code=403,
            detail="legacy auth not permitted on this endpoint",
        )
    if not auth.sub:
        raise HTTPException(status_code=401, detail="auth.sub required")


def _is_room_admin(room_data: dict, actor: str) -> bool:
    """True if ``actor`` is a room creator or has role=admin in members."""
    if room_data.get("created_by") == actor:
        return True
    members = room_data.get("members") or {}
    if isinstance(members, dict):
        role = members.get(actor)
        return role == "admin"
    return False


def _policy_check(
    request: Request,
    auth: AuthContext,
    action: str,
    resource: str,
    members: list[str],
) -> None:
    policy_dir = getattr(request.app.state, "policy_dir", None)
    policy = load_policy_for_tenant(_tid(auth), policy_dir=policy_dir)
    ctx = PolicyContext(
        actor=auth.sub or "",
        action=action,
        resource=resource,
        role=auth.role or "agent",
        tenant_id=_tid(auth),
        extra={"room_members": members},
    )
    result = evaluate_scoped(ctx, policy)
    if result.effective_decision != Decision.ALLOW:
        raise HTTPException(
            status_code=403,
            detail=f"tool_catalog policy: {result.reason}",
        )


def _members_list(room_data: dict) -> list[str]:
    raw = room_data.get("members") or {}
    if isinstance(raw, dict):
        return list(raw.keys())
    return list(raw)


@router.post("/v1/rooms/{room_id}/tools")
async def register_tool(
    room_id: str,
    body: ToolRegistration,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Register an MCP server in the room. Room admin only."""
    _require_jwt(auth)
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, room_id)
    if not _is_room_admin(room_data, auth.sub or ""):
        raise HTTPException(
            status_code=403,
            detail="only room admins may register tools",
        )
    members = _members_list(room_data)
    _policy_check(request, auth, "tools:register", rid, members)

    rate_limit_svc = request.app.state.rate_limit_service
    if not await rate_limit_svc.check_with_limit(
        tid, f"tools:register:{auth.sub}", 30,
    ):
        raise HTTPException(
            status_code=429, detail="tool registration rate limit",
        )

    # 2026-05-16 audit fix: write audit before mutation. Refuse the mutation
    # in production if the audit ledger is down — silent try/except: pass
    # leaves a compliance gap.
    audit_svc = getattr(request.app.state, "audit_service", None)
    if audit_svc is not None:
        try:
            await audit_svc.record(
                tenant_id=tid,
                message_id=_uuid.uuid4(),
                event_type="tools:register",
                actor=auth.sub,
                target=body.name,
                room_id=rid,
                room_name=room_data.get("name"),
                details={
                    "tool_name": body.name,
                    "access": body.access,
                },
            )
        except Exception as exc:
            if os.environ.get("DATABASE_URL"):
                raise HTTPException(
                    status_code=503,
                    detail="audit ledger unavailable; refusing mutation",
                ) from exc
            logger.warning("audit record failed in dev mode", reason=str(exc))

    svc = _tool_catalog_svc(request)
    try:
        record = await svc.register(
            tid, rid,
            name=body.name,
            url=str(body.url),
            registered_by=auth.sub or "",
            access=body.access,
            description=body.description,
        )
    except DuplicateToolError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ToolCatalogError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"ok": True, "tool": record}


@router.get("/v1/rooms/{room_id}/tools")
async def list_tools(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """List MCP servers registered in the room. Room members only."""
    _require_jwt(auth)
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, room_id)
    members = _members_list(room_data)
    _policy_check(request, auth, "tools:list", rid, members)
    svc = _tool_catalog_svc(request)
    tools = await svc.list(tid, rid)
    return {"room_id": rid, "count": len(tools), "tools": tools}


@router.delete("/v1/rooms/{room_id}/tools/{name}")
async def remove_tool(
    room_id: str,
    name: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Remove an MCP server registration. Room admin only."""
    _require_jwt(auth)
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, room_id)
    if not _is_room_admin(room_data, auth.sub or ""):
        raise HTTPException(
            status_code=403,
            detail="only room admins may remove tools",
        )
    members = _members_list(room_data)
    _policy_check(request, auth, "tools:remove", rid, members)

    # 2026-05-16 audit fix: record intent before mutation. Whether the tool
    # actually existed is unknown until svc.remove runs, but the intent to
    # remove must be auditable regardless.
    audit_svc = getattr(request.app.state, "audit_service", None)
    if audit_svc is not None:
        try:
            await audit_svc.record(
                tenant_id=tid,
                message_id=_uuid.uuid4(),
                event_type="tools:remove",
                actor=auth.sub,
                target=name,
                room_id=rid,
                room_name=room_data.get("name"),
                details={"tool_name": name},
            )
        except Exception as exc:
            if os.environ.get("DATABASE_URL"):
                raise HTTPException(
                    status_code=503,
                    detail="audit ledger unavailable; refusing mutation",
                ) from exc
            logger.warning("audit record failed in dev mode", reason=str(exc))

    svc = _tool_catalog_svc(request)
    removed = await svc.remove(tid, rid, name)
    if not removed:
        raise HTTPException(
            status_code=404, detail=f"tool {name!r} not found",
        )

    return {"ok": True, "removed": name}


__all__ = ["router"]
