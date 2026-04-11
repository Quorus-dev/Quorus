"""Auth routes — API key -> JWT exchange endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from murmur.admin.models import ApiKey, Participant, Tenant
from murmur.auth.tokens import create_jwt, extract_key_prefix, verify_api_key
from murmur.storage.postgres import get_db_session

logger = logging.getLogger("murmur.auth")
router = APIRouter(prefix="/v1/auth", tags=["auth"])


class TokenRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    token: str
    token_type: str = "Bearer"
    expires_in: int


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

        from murmur.auth.tokens import JWT_TTL_SECONDS

        token = create_jwt(
            sub=participant.name,
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            role=participant.role,
        )

        logger.info(
            "JWT issued for %s (tenant=%s, role=%s)",
            participant.name,
            tenant.slug,
            participant.role,
        )

        return TokenResponse(
            token=token,
            expires_in=JWT_TTL_SECONDS,
        )
