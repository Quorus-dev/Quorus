"""Persistent memory routes — Plan v8 Phase 1 OS primitive C.

Four endpoints:

* ``PUT /v1/memory/{participant}/{room_id}/{key}`` — set a value (writer
  must be ``participant`` itself, or admin).
* ``GET /v1/memory/{participant}/{room_id}/{key}`` — read by key. Visibility
  filter applies: ``private`` is owner-only; ``room`` requires room
  membership; ``public`` requires same tenant.
* ``GET /v1/memory/{participant}/{room_id}`` — list all readable entries
  for the (participant, room) scope.
* ``DELETE /v1/memory/{participant}/{room_id}/{key}`` — remove (GDPR).
  Owner or admin only.

Storage in :class:`quorus.services.persistent_memory_svc.PersistentMemorySvc`.
"""
from __future__ import annotations

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
from quorus.routes.room_auth import require_room_member
from quorus.services.persistent_memory_svc import (
    EntryCapError,
    MemoryError_,
    PersistentMemorySvc,
    ValueTooLargeError,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    VISIBILITY_ROOM,
)

router = APIRouter()
_LEGACY_TENANT = "_legacy"


class MemorySetBody(BaseModel):
    """Body for ``PUT /v1/memory/{participant}/{room_id}/{key}``."""

    model_config = {"extra": "forbid"}

    value: Any
    visibility: str = Field(default=VISIBILITY_PRIVATE, max_length=20)

    @field_validator("visibility")
    @classmethod
    def _norm_visibility(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {VISIBILITY_PRIVATE, VISIBILITY_ROOM, VISIBILITY_PUBLIC}:
            raise ValueError(
                "visibility must be private|room|public"
            )
        return v


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def _memory_svc(request: Request) -> PersistentMemorySvc:
    svc = getattr(request.app.state, "persistent_memory_service", None)
    if svc is None:
        svc = PersistentMemorySvc()
        request.app.state.persistent_memory_service = svc
    return svc


def _require_jwt(auth: AuthContext) -> None:
    if auth.is_legacy:
        raise HTTPException(
            status_code=403,
            detail="legacy auth not permitted on this endpoint",
        )
    if not auth.sub:
        raise HTTPException(status_code=401, detail="auth.sub required")


def _members_list(room_data: dict) -> list[str]:
    raw = room_data.get("members") or {}
    if isinstance(raw, dict):
        return list(raw.keys())
    return list(raw)


def _can_read(
    entry: dict, viewer: str, members: list[str], viewer_role: str,
) -> bool:
    """Visibility check. Owner is always allowed."""
    owner = entry.get("owner")
    if owner == viewer:
        return True
    if viewer_role == "admin":
        return True
    vis = entry.get("visibility", VISIBILITY_PRIVATE)
    if vis == VISIBILITY_PUBLIC:
        return True
    if vis == VISIBILITY_ROOM and viewer in members:
        return True
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
            status_code=403, detail=f"memory policy: {result.reason}",
        )


@router.put("/v1/memory/{participant}/{room_id}/{key}")
async def set_memory(
    participant: str,
    room_id: str,
    key: str,
    body: MemorySetBody,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Set a value. Owner-only writes (or admin)."""
    _require_jwt(auth)
    if participant != auth.sub and auth.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="cannot write memory for another participant",
        )
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, room_id)
    members = _members_list(room_data)
    _policy_check(request, auth, "memory:write", rid, members)

    rate_limit_svc = request.app.state.rate_limit_service
    if not await rate_limit_svc.check_with_limit(
        tid, f"memory:write:{auth.sub}", 60,
    ):
        raise HTTPException(
            status_code=429, detail="memory write rate limit",
        )

    svc = _memory_svc(request)
    try:
        entry = await svc.set(
            tid, participant, rid, key, body.value,
            visibility=body.visibility,
        )
    except ValueTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except EntryCapError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except MemoryError_ as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Tag owner so downstream visibility checks know who wrote it.
    entry["owner"] = participant

    audit_svc = getattr(request.app.state, "audit_service", None)
    if audit_svc is not None:
        try:
            await audit_svc.record(
                tenant_id=tid,
                message_id=_uuid.uuid4(),
                event_type="memory:write",
                actor=auth.sub,
                target=key,
                room_id=rid,
                room_name=room_data.get("name"),
                details={
                    "participant": participant,
                    "key": key,
                    "visibility": body.visibility,
                    "size_bytes": entry.get("size_bytes"),
                },
            )
        except Exception:
            pass
    return {"ok": True, "entry": entry}


@router.get("/v1/memory/{participant}/{room_id}/{key}")
async def get_memory(
    participant: str,
    room_id: str,
    key: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Read a value if visibility allows."""
    _require_jwt(auth)
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, room_id)
    members = _members_list(room_data)
    _policy_check(request, auth, "memory:read", rid, members)

    svc = _memory_svc(request)
    entry = await svc.get(tid, participant, rid, key)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=f"no entry for key {key!r}",
        )
    entry["owner"] = participant
    if not _can_read(entry, auth.sub or "", members, auth.role or ""):
        # Don't leak existence — same shape as 404 to prevent oracle.
        raise HTTPException(
            status_code=404, detail=f"no entry for key {key!r}",
        )
    return {"entry": entry}


@router.get("/v1/memory/{participant}/{room_id}")
async def list_memory(
    participant: str,
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """List entries readable by the caller (visibility-filtered)."""
    _require_jwt(auth)
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, room_id)
    members = _members_list(room_data)
    _policy_check(request, auth, "memory:read", rid, members)
    svc = _memory_svc(request)
    entries = await svc.list(tid, participant, rid)
    visible = []
    for e in entries:
        e["owner"] = participant
        if _can_read(e, auth.sub or "", members, auth.role or ""):
            visible.append(e)
    return {
        "participant": participant,
        "room_id": rid,
        "count": len(visible),
        "entries": visible,
    }


@router.delete("/v1/memory/{participant}/{room_id}/{key}")
async def delete_memory(
    participant: str,
    room_id: str,
    key: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """GDPR-compliant delete. Owner or admin only."""
    _require_jwt(auth)
    if participant != auth.sub and auth.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="cannot delete memory for another participant",
        )
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, room_id)
    members = _members_list(room_data)
    _policy_check(request, auth, "memory:delete", rid, members)

    svc = _memory_svc(request)
    removed = await svc.delete(tid, participant, rid, key)
    if not removed:
        raise HTTPException(
            status_code=404, detail=f"no entry for key {key!r}",
        )

    audit_svc = getattr(request.app.state, "audit_service", None)
    if audit_svc is not None:
        try:
            await audit_svc.record(
                tenant_id=tid,
                message_id=_uuid.uuid4(),
                event_type="memory:delete",
                actor=auth.sub,
                target=key,
                room_id=rid,
                room_name=room_data.get("name"),
                details={"participant": participant, "key": key},
            )
        except Exception:
            pass
    return {"ok": True, "deleted": key}


__all__ = ["router"]
