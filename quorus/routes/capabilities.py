"""Capability discovery routes — Plan v8 Phase 1 OS primitive A.

Three endpoints:

* ``POST /v1/capabilities/{participant}`` — agent publishes its manifest.
  An agent may only publish its OWN manifest unless the caller has
  ``role=admin``. Cedar policy gate runs after the auth check.

* ``GET /v1/capabilities/{participant}`` — fetch a single manifest.
  Open to any tenant member (callers must auth, but no membership check
  beyond "same tenant").

* ``GET /v1/capabilities/search?has=python+pytest`` — find peers whose
  ``capabilities`` list contains every supplied substring (URL-decoded
  + split on spaces or commas). Returns participants in the caller's
  tenant only.

Storage is ephemeral — see :class:`quorus.services.capability_svc.CapabilitySvc`.
"""
from __future__ import annotations

import os
import uuid as _uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.auth.policy import (
    Decision,
    PolicyContext,
    evaluate_scoped,
    load_policy_for_tenant,
)
from quorus.services.capability_svc import CapabilityError, CapabilitySvc

router = APIRouter()
logger = structlog.get_logger("quorus.routes.capabilities")
_LEGACY_TENANT = "_legacy"

# Cap manifest fields so a noisy agent can't blow memory.
_MAX_LIST_ITEMS = 64
_MAX_STR_LEN = 200


class CapabilityManifest(BaseModel):
    """Strict body schema for ``POST /v1/capabilities/{participant}``."""

    model_config = {"extra": "forbid"}

    description: str = Field(default="", max_length=1000)
    supported_languages: list[str] = Field(default_factory=list)
    supported_frameworks: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)

    @field_validator(
        "supported_languages", "supported_frameworks", "capabilities",
    )
    @classmethod
    def _bound_list(cls, v: list[str]) -> list[str]:
        if len(v) > _MAX_LIST_ITEMS:
            raise ValueError(
                f"list supports at most {_MAX_LIST_ITEMS} entries"
            )
        out: list[str] = []
        for item in v:
            s = str(item).strip()
            if not s:
                continue
            if len(s) > _MAX_STR_LEN:
                raise ValueError(
                    f"entry exceeds {_MAX_STR_LEN} chars"
                )
            out.append(s)
        return out


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def _capability_svc(request: Request) -> CapabilitySvc:
    svc = getattr(request.app.state, "capability_service", None)
    if svc is None:
        svc = CapabilitySvc()
        request.app.state.capability_service = svc
    return svc


def _require_jwt(auth: AuthContext) -> None:
    if auth.is_legacy:
        raise HTTPException(
            status_code=403,
            detail="legacy auth not permitted on this endpoint",
        )
    if not auth.sub:
        raise HTTPException(status_code=401, detail="auth.sub required")


def _policy_check(
    request: Request, auth: AuthContext, action: str, resource: str,
) -> None:
    policy_dir = getattr(request.app.state, "policy_dir", None)
    policy = load_policy_for_tenant(_tid(auth), policy_dir=policy_dir)
    ctx = PolicyContext(
        actor=auth.sub or "",
        action=action,
        resource=resource,
        role=auth.role or "agent",
        tenant_id=_tid(auth),
    )
    result = evaluate_scoped(ctx, policy)
    if result.effective_decision != Decision.ALLOW:
        raise HTTPException(
            status_code=403,
            detail=f"capabilities policy: {result.reason}",
        )


@router.post("/v1/capabilities/{participant}")
async def publish_capability(
    participant: str,
    body: CapabilityManifest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Publish or replace ``participant``'s capability manifest."""
    _require_jwt(auth)
    if not participant.strip():
        raise HTTPException(
            status_code=400, detail="participant required",
        )
    # Self-write only, unless admin role.
    if participant != auth.sub and auth.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="cannot publish manifest for another participant",
        )
    _policy_check(request, auth, "capabilities:write", participant)

    rate_limit_svc = request.app.state.rate_limit_service
    if not await rate_limit_svc.check_with_limit(
        _tid(auth), f"capabilities:publish:{auth.sub}", 30,
    ):
        raise HTTPException(
            status_code=429, detail="capability publish rate limit",
        )

    # 2026-05-16 audit fix: record intent before mutation, refuse in production
    # if audit unavailable.
    audit_svc = getattr(request.app.state, "audit_service", None)
    if audit_svc is not None:
        try:
            await audit_svc.record(
                tenant_id=_tid(auth),
                message_id=_uuid.uuid4(),
                event_type="capabilities:publish",
                actor=auth.sub,
                target=participant,
                details={
                    "capabilities_count": len(body.capabilities or []),
                },
            )
        except Exception as exc:
            if os.environ.get("DATABASE_URL"):
                raise HTTPException(
                    status_code=503,
                    detail="audit ledger unavailable; refusing mutation",
                ) from exc
            logger.warning("audit record failed in dev mode", reason=str(exc))

    svc = _capability_svc(request)
    try:
        manifest = await svc.publish(
            _tid(auth), participant, body.model_dump(),
        )
    except CapabilityError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"ok": True, "manifest": manifest}


@router.get("/v1/capabilities/search")
async def search_capabilities(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    has: str | None = Query(default=None),
):
    """Return manifests in caller's tenant matching ALL ``has`` tokens."""
    _require_jwt(auth)
    _policy_check(request, auth, "capabilities:read", "*")
    tokens: list[str] = []
    if has:
        # Accept space- or comma-delimited query (URL-encoded "+" → " ").
        tokens = [t for t in has.replace(",", " ").split() if t]
    svc = _capability_svc(request)
    matches = await svc.search(_tid(auth), tokens)
    return {
        "query": tokens,
        "count": len(matches),
        "manifests": matches,
    }


@router.get("/v1/capabilities/{participant}")
async def get_capability(
    participant: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Fetch a single participant's capability manifest."""
    _require_jwt(auth)
    if not participant.strip():
        raise HTTPException(
            status_code=400, detail="participant required",
        )
    _policy_check(request, auth, "capabilities:read", participant)
    svc = _capability_svc(request)
    manifest = await svc.get(_tid(auth), participant)
    if manifest is None:
        raise HTTPException(
            status_code=404,
            detail=f"no manifest registered for {participant!r}",
        )
    return {"manifest": manifest}


__all__ = ["router"]
