"""Admin routes — tenant, participant, and API key CRUD."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from quorus.admin.models import ApiKey, Participant, Tenant
from quorus.auth.middleware import AuthContext, require_role, verify_auth
from quorus.auth.tokens import generate_api_key
from quorus.storage.postgres import get_db_session

logger = logging.getLogger("quorus.admin")
router = APIRouter(prefix="/v1", tags=["admin"])

BOOTSTRAP_SECRET = os.environ.get("BOOTSTRAP_SECRET", "")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}[a-z0-9]$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _enforce_tenant_isolation(auth: AuthContext, slug: str) -> None:
    """Verify the authenticated user belongs to the requested tenant.

    Legacy auth (RELAY_SECRET) is exempt — it has cross-tenant admin access.
    JWT users must match the tenant_slug in their token.
    """
    if auth.is_legacy:
        return
    if auth.tenant_slug and auth.tenant_slug != slug:
        raise HTTPException(
            status_code=403,
            detail=f"Tenant mismatch: token grants access to '{auth.tenant_slug}', not '{slug}'",
        )


def _validate_slug(value: str) -> str:
    if not _SLUG_RE.match(value):
        raise ValueError(
            "Slug must be 2-64 lowercase alphanumeric chars, hyphens, or underscores"
        )
    return value


def _validate_name(value: str) -> str:
    if not value or len(value) > 64 or not _NAME_RE.match(value):
        raise ValueError("Name must be 1-64 alphanumeric characters, hyphens, or underscores")
    return value


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class CreateTenantRequest(BaseModel):
    slug: str
    display_name: str = ""

    @field_validator("slug")
    @classmethod
    def check_slug(cls, v: str) -> str:
        return _validate_slug(v)


class TenantResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    created_at: str


class CreateParticipantRequest(BaseModel):
    name: str
    role: str = "user"

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str) -> str:
        if v not in {"admin", "user"}:
            raise ValueError("role must be 'admin' or 'user'")
        return v


class ParticipantResponse(BaseModel):
    id: str
    name: str
    role: str
    created_at: str


class CreateKeyRequest(BaseModel):
    label: str | None = None


class KeyResponse(BaseModel):
    id: str
    key_prefix: str
    label: str | None
    created_at: str


class NewKeyResponse(KeyResponse):
    raw_key: str  # Only returned once on creation


# ---------------------------------------------------------------------------
# Bootstrap: create first tenant (requires BOOTSTRAP_SECRET)
# ---------------------------------------------------------------------------


def _verify_bootstrap(request: Request) -> None:
    """Verify the Bootstrap-Secret header."""
    if not BOOTSTRAP_SECRET:
        raise HTTPException(
            status_code=503,
            detail="BOOTSTRAP_SECRET not configured — cannot create tenants",
        )
    provided = request.headers.get("Bootstrap-Secret", "")
    if provided != BOOTSTRAP_SECRET:
        raise HTTPException(status_code=403, detail="Invalid bootstrap secret")


@router.post("/tenants", response_model=TenantResponse)
async def create_tenant(req: CreateTenantRequest, request: Request):
    """Create a new tenant. Requires Bootstrap-Secret header."""
    _verify_bootstrap(request)

    async with get_db_session() as session:
        # Check uniqueness
        existing = await session.execute(
            select(Tenant).where(Tenant.slug == req.slug)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Tenant slug already exists")

        tenant = Tenant(slug=req.slug, display_name=req.display_name)
        session.add(tenant)
        await session.flush()

        logger.info("Tenant created: %s (%s)", req.slug, tenant.id)
        return TenantResponse(
            id=tenant.id,
            slug=tenant.slug,
            display_name=tenant.display_name,
            created_at=tenant.created_at.isoformat(),
        )


@router.get("/tenants/{slug}", response_model=TenantResponse)
async def get_tenant(slug: str, auth: AuthContext = Depends(verify_auth)):
    """Get tenant details by slug. Requires matching tenant or admin."""
    _enforce_tenant_isolation(auth, slug)
    async with get_db_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.slug == slug)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return TenantResponse(
            id=tenant.id,
            slug=tenant.slug,
            display_name=tenant.display_name,
            created_at=tenant.created_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------


@router.post(
    "/tenants/{slug}/participants",
    response_model=ParticipantResponse,
)
async def create_participant(
    slug: str,
    req: CreateParticipantRequest,
    auth: AuthContext = Depends(verify_auth),
):
    """Create a participant within a tenant. Requires admin role."""
    require_role(auth, "admin")
    _enforce_tenant_isolation(auth, slug)

    async with get_db_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        # Check uniqueness within tenant
        existing = await session.execute(
            select(Participant).where(
                Participant.tenant_id == tenant.id,
                Participant.name == req.name,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409, detail="Participant name already exists in this tenant"
            )

        participant = Participant(
            tenant_id=tenant.id,
            name=req.name,
            role=req.role,
        )
        session.add(participant)
        await session.flush()

        logger.info(
            "Participant created: %s in tenant %s (role=%s)",
            req.name, slug, req.role,
        )
        return ParticipantResponse(
            id=participant.id,
            name=participant.name,
            role=participant.role,
            created_at=participant.created_at.isoformat(),
        )


@router.get("/tenants/{slug}/participants")
async def list_participants(
    slug: str,
    auth: AuthContext = Depends(verify_auth),
):
    """List all participants in a tenant. Requires admin role."""
    require_role(auth, "admin")
    _enforce_tenant_isolation(auth, slug)

    async with get_db_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        result = await session.execute(
            select(Participant).where(Participant.tenant_id == tenant.id)
        )
        participants = result.scalars().all()
        return [
            ParticipantResponse(
                id=p.id,
                name=p.name,
                role=p.role,
                created_at=p.created_at.isoformat(),
            )
            for p in participants
        ]


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------


@router.post(
    "/tenants/{slug}/participants/{name}/keys",
    response_model=NewKeyResponse,
)
async def issue_api_key(
    slug: str,
    name: str,
    req: CreateKeyRequest,
    auth: AuthContext = Depends(verify_auth),
):
    """Issue a new API key for a participant. Returns the raw key once."""
    require_role(auth, "admin")
    _enforce_tenant_isolation(auth, slug)

    async with get_db_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        participant = (
            await session.execute(
                select(Participant).where(
                    Participant.tenant_id == tenant.id,
                    Participant.name == name,
                )
            )
        ).scalar_one_or_none()
        if not participant:
            raise HTTPException(status_code=404, detail="Participant not found")

        raw_key, prefix, key_hash = generate_api_key()
        api_key = ApiKey(
            participant_id=participant.id,
            label=req.label,
            key_prefix=prefix,
            key_hash=key_hash,
        )
        session.add(api_key)
        await session.flush()

        logger.info("API key issued for %s/%s (prefix=%s)", slug, name, prefix)
        return NewKeyResponse(
            id=api_key.id,
            key_prefix=api_key.key_prefix,
            label=api_key.label,
            raw_key=raw_key,
            created_at=api_key.created_at.isoformat(),
        )


@router.get("/tenants/{slug}/participants/{name}/keys")
async def list_api_keys(
    slug: str,
    name: str,
    auth: AuthContext = Depends(verify_auth),
):
    """List API keys for a participant (without hashes)."""
    require_role(auth, "admin")
    _enforce_tenant_isolation(auth, slug)

    async with get_db_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        participant = (
            await session.execute(
                select(Participant).where(
                    Participant.tenant_id == tenant.id,
                    Participant.name == name,
                )
            )
        ).scalar_one_or_none()
        if not participant:
            raise HTTPException(status_code=404, detail="Participant not found")

        result = await session.execute(
            select(ApiKey).where(ApiKey.participant_id == participant.id)
        )
        keys = result.scalars().all()
        return [
            KeyResponse(
                id=k.id,
                key_prefix=k.key_prefix,
                label=k.label,
                created_at=k.created_at.isoformat(),
            )
            for k in keys
        ]


@router.delete("/tenants/{slug}/participants/{name}/keys/{key_id}")
async def revoke_api_key(
    slug: str,
    name: str,
    key_id: str,
    auth: AuthContext = Depends(verify_auth),
):
    """Revoke an API key."""
    require_role(auth, "admin")
    _enforce_tenant_isolation(auth, slug)

    async with get_db_session() as session:
        # Verify the key belongs to the correct tenant/participant path
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        participant = (
            await session.execute(
                select(Participant).where(
                    Participant.tenant_id == tenant.id,
                    Participant.name == name,
                )
            )
        ).scalar_one_or_none()
        if not participant:
            raise HTTPException(status_code=404, detail="Participant not found")

        api_key = await session.get(ApiKey, key_id)
        if not api_key or api_key.participant_id != participant.id:
            raise HTTPException(status_code=404, detail="API key not found")

        if api_key.revoked_at is not None:
            raise HTTPException(status_code=400, detail="API key already revoked")

        api_key.revoked_at = datetime.now(timezone.utc)
        await session.flush()

        logger.info("API key revoked: %s (prefix=%s)", key_id, api_key.key_prefix)
        return {"status": "revoked", "key_id": key_id}
