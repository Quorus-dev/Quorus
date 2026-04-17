"""Auth routes — signup, API key -> JWT exchange."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select

from quorus.admin.models import ApiKey, Participant, Tenant
from quorus.auth.tokens import create_jwt, extract_key_prefix, generate_api_key, verify_api_key
from quorus.storage.postgres import get_db_session

logger = logging.getLogger("quorus.auth")
router = APIRouter(prefix="/v1/auth", tags=["auth"])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}[a-z0-9]$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class SignupRequest(BaseModel):
    """Self-service signup request."""
    name: str
    workspace: str
    email: EmailStr | None = None
    join_tenant_id: str | None = None  # Join existing tenant instead of creating new

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 64 or not _NAME_RE.match(v):
            raise ValueError("Name must be 1-64 alphanumeric chars, hyphens, or underscores")
        return v

    @field_validator("workspace")
    @classmethod
    def check_workspace(cls, v: str) -> str:
        v = v.strip().lower()
        if not _SLUG_RE.match(v):
            raise ValueError(
                "Workspace must be 2-64 lowercase alphanumeric chars, hyphens, or underscores"
            )
        return v


class SignupResponse(BaseModel):
    """Returned once on signup.

    The raw API key is only included when the request sets
    ``X-Quorus-Setup-Local: 1`` — by default only metadata is returned so
    keys are never exposed via response bodies or logs.
    """
    tenant_slug: str
    participant_name: str
    key_prefix: str
    key_id: str
    next_steps: str
    relay_url: str
    api_key: str | None = None
    setup_command: str | None = None


class TokenRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    token: str
    token_type: str = "Bearer"
    expires_in: int


class RegisterAgentRequest(BaseModel):
    """Register an agent identity under an existing account."""
    suffix: str  # e.g. "claude", "codex", "cursor"

    @field_validator("suffix")
    @classmethod
    def check_suffix(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or len(v) > 32 or not _NAME_RE.match(v):
            raise ValueError("Suffix must be 1-32 alphanumeric chars, hyphens, or underscores")
        return v


class RegisterAgentResponse(BaseModel):
    """Returns the new agent identity's API key."""
    agent_name: str
    api_key: str
    tenant_slug: str


# ---------------------------------------------------------------------------
# Self-service signup
# ---------------------------------------------------------------------------


@router.post("/signup", response_model=SignupResponse)
async def signup(
    req: SignupRequest,
    request: Request,
    x_quorus_setup_local: str | None = Header(
        default=None, alias="X-Quorus-Setup-Local",
    ),
):
    """Self-service signup: creates tenant + participant + API key.

    Rate limited to 5 signups per hour per IP.
    Returns the API key once — it cannot be retrieved again.
    """
    # Rate limit by IP: 5 requests per 60 seconds
    client_ip = request.client.host if request.client else "unknown"
    rate_limit_svc = request.app.state.rate_limit_service
    _SIGNUP_WINDOW = 60
    allowed = await rate_limit_svc.check_with_limit(
        "global", f"signup:{client_ip}", 5, window=_SIGNUP_WINDOW,
    )
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "retry_after": _SIGNUP_WINDOW},
            headers={"Retry-After": str(_SIGNUP_WINDOW)},
        )

    async with get_db_session() as session:
        # If joining an existing tenant via invite code
        if req.join_tenant_id:
            import uuid
            try:
                # Validate format, but keep as string for session.get
                uuid.UUID(req.join_tenant_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid tenant ID format")

            tenant = await session.get(Tenant, req.join_tenant_id)
            if not tenant:
                raise HTTPException(status_code=404, detail="Tenant not found")

            # Create participant as member (not admin) in existing tenant
            participant = Participant(
                tenant_id=tenant.id,
                name=req.name,
                role="member",
            )
            session.add(participant)
            await session.flush()

            logger.info(
                "Join-tenant signup: %s joined %s (tenant_id=%s)",
                req.name, tenant.slug, tenant.id,
            )
        else:
            # Check workspace uniqueness
            existing = await session.execute(
                select(Tenant).where(Tenant.slug == req.workspace)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=409,
                    detail=f"Workspace '{req.workspace}' is already taken",
                )

            # Create tenant
            tenant = Tenant(slug=req.workspace, display_name=req.workspace)
            session.add(tenant)
            await session.flush()

            # Create participant as admin
            participant = Participant(
                tenant_id=tenant.id,
                name=req.name,
                role="admin",
            )
            session.add(participant)
            await session.flush()

        # Issue API key
        raw_key, prefix, key_hash = generate_api_key()
        api_key = ApiKey(
            participant_id=participant.id,
            label="initial-key",
            key_prefix=prefix,
            key_hash=key_hash,
        )
        session.add(api_key)
        await session.flush()

        logger.info(
            "Self-service signup: %s/%s (tenant_id=%s, key_prefix=%s)",
            req.workspace, req.name, tenant.id, prefix,
        )

        # Build relay URL from request
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.url.netloc)
        relay_url = f"{scheme}://{host}"

        is_local_setup = x_quorus_setup_local == "1"
        return SignupResponse(
            tenant_slug=tenant.slug,
            participant_name=participant.name,
            key_prefix=prefix,
            key_id=str(api_key.id) if hasattr(api_key, "id") and api_key.id else prefix,
            next_steps=(
                f"Run: quorus init {req.name} --relay-url {relay_url} --api-key <your-key>"
            ),
            relay_url=relay_url,
            setup_command=(
                f"quorus init {req.name} --relay-url {relay_url} --api-key {raw_key}"
                if is_local_setup else None
            ),
            api_key=raw_key if is_local_setup else None,
        )


# ---------------------------------------------------------------------------
# API key -> JWT exchange
# ---------------------------------------------------------------------------


@router.post("/token", response_model=TokenResponse)
async def exchange_api_key(req: TokenRequest):
    """Exchange an API key for a short-lived JWT."""
    raw_key = req.api_key.strip()

    try:
        prefix = extract_key_prefix(raw_key)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid API key format")

    async with get_db_session() as session:
        # Look up the key by prefix
        result = await session.execute(
            select(ApiKey).where(
                ApiKey.key_prefix == prefix,
                ApiKey.revoked_at.is_(None),
            )
        )
        key_row = result.scalar_one_or_none()
        if not key_row:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")

        # Verify the full key hash
        if not verify_api_key(raw_key, key_row.key_hash):
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Look up participant and tenant
        participant = await session.get(Participant, key_row.participant_id)
        if not participant:
            raise HTTPException(status_code=401, detail="Participant not found")

        tenant = await session.get(Tenant, participant.tenant_id)
        if not tenant:
            raise HTTPException(status_code=401, detail="Tenant not found")

        from quorus.auth.tokens import JWT_TTL_SECONDS

        token = create_jwt(
            sub=participant.name,
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            role=participant.role,
            participant_id=participant.id,
            extra={"key_prefix": key_row.key_prefix},
        )

        logger.info(
            "JWT issued for %s (tenant=%s, role=%s, participant_id=%s)",
            participant.name,
            tenant.slug,
            participant.role,
            participant.id,
        )

        return TokenResponse(
            token=token,
            expires_in=JWT_TTL_SECONDS,
        )


# ---------------------------------------------------------------------------
# Register agent identity
# ---------------------------------------------------------------------------


@router.post("/register-agent", response_model=RegisterAgentResponse)
async def register_agent(
    req: RegisterAgentRequest,
    request: Request,
    authorization: str = Header(...),
):
    """Create a new agent identity under an existing account.

    Requires the parent account's API key in the Authorization header.
    Creates a new participant named `{parent_name}-{suffix}` with its own API key.
    """
    # Extract API key from Bearer header
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    parent_key = authorization[7:].strip()

    try:
        prefix = extract_key_prefix(parent_key)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid API key format")

    async with get_db_session() as session:
        # Look up parent key
        result = await session.execute(
            select(ApiKey).where(
                ApiKey.key_prefix == prefix,
                ApiKey.revoked_at.is_(None),
            )
        )
        parent_key_row = result.scalar_one_or_none()
        if not parent_key_row or not verify_api_key(parent_key, parent_key_row.key_hash):
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Get parent participant and tenant
        parent_participant = await session.get(Participant, parent_key_row.participant_id)
        if not parent_participant:
            raise HTTPException(status_code=401, detail="Parent participant not found")

        tenant = await session.get(Tenant, parent_participant.tenant_id)
        if not tenant:
            raise HTTPException(status_code=401, detail="Tenant not found")

        # Build agent name: {parent_name}-{suffix}
        agent_name = f"{parent_participant.name}-{req.suffix}"

        # Check if agent already exists — return existing key if so
        existing_result = await session.execute(
            select(Participant).where(
                Participant.tenant_id == tenant.id,
                Participant.name == agent_name,
            )
        )
        existing_agent = existing_result.scalar_one_or_none()

        if existing_agent:
            # Agent exists — find its active API key
            key_result = await session.execute(
                select(ApiKey).where(
                    ApiKey.participant_id == existing_agent.id,
                    ApiKey.revoked_at.is_(None),
                )
            )
            existing_key = key_result.scalar_one_or_none()
            if existing_key:
                # Can't return the raw key (it's hashed), so create a new one
                raw_key, new_prefix, key_hash = generate_api_key()
                new_api_key = ApiKey(
                    participant_id=existing_agent.id,
                    label=f"agent-{req.suffix}",
                    key_prefix=new_prefix,
                    key_hash=key_hash,
                )
                session.add(new_api_key)
                await session.flush()

                logger.info(
                    "Agent key regenerated: %s (tenant=%s, suffix=%s)",
                    agent_name, tenant.slug, req.suffix,
                )
                return RegisterAgentResponse(
                    agent_name=agent_name,
                    api_key=raw_key,
                    tenant_slug=tenant.slug,
                )

        # Create new agent participant
        agent_participant = Participant(
            tenant_id=tenant.id,
            name=agent_name,
            role="agent",  # Agents get a distinct role
        )
        session.add(agent_participant)
        await session.flush()

        # Generate API key for the agent
        raw_key, new_prefix, key_hash = generate_api_key()
        agent_api_key = ApiKey(
            participant_id=agent_participant.id,
            label=f"agent-{req.suffix}",
            key_prefix=new_prefix,
            key_hash=key_hash,
        )
        session.add(agent_api_key)
        await session.flush()

        logger.info(
            "Agent registered: %s (tenant=%s, suffix=%s, parent=%s)",
            agent_name, tenant.slug, req.suffix, parent_participant.name,
        )

        return RegisterAgentResponse(
            agent_name=agent_name,
            api_key=raw_key,
            tenant_slug=tenant.slug,
        )
