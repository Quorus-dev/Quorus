"""Claim a name on a shared-secret relay and turn its key into a JWT."""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.auth.participant_keys import ParticipantKeyStore, data_dir
from quorus.auth.tokens import JWT_TTL_SECONDS, create_jwt
from quorus.config import get_real_ip
from quorus.routes.helpers import _validate_name

router = APIRouter(prefix="/v1/auth", tags=["auth"])
LEGACY_TENANT = "_legacy"  # the tenant every shared-secret room lives in
CLAIMS_PER_HOUR = 10


class ClaimNameRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _validate_name(v)


def _store(request: Request) -> ParticipantKeyStore:
    """The claims store, opened on first use; only shared-secret relays have one."""
    store = getattr(request.app.state, "participant_keys", None)
    if store is None:
        if os.environ.get("DATABASE_URL"):
            raise HTTPException(status_code=404,
                                detail="this relay issues API keys; use `quorus init --api-key`")
        store = ParticipantKeyStore(data_dir())
        request.app.state.participant_keys = store
    return store


@router.post("/claim-name")
async def claim_name(req: ClaimNameRequest, request: Request,
                     auth: AuthContext = Depends(verify_auth)) -> dict:
    """Shared-secret holders claim a name once; the key is returned once."""
    store = _store(request)
    if not auth.is_legacy:
        raise HTTPException(status_code=403, detail="only the relay's shared secret can claim")
    allowed = await request.app.state.rate_limit_service.check_with_limit(
        "global", f"claim-name:{get_real_ip(request)}", CLAIMS_PER_HOUR, window=3600)
    if not allowed:
        raise HTTPException(status_code=429, detail="too many name claims; retry in an hour")
    key = await asyncio.to_thread(store.claim, req.name)
    if key is None:
        raise HTTPException(status_code=409, detail=(
            f"the name {req.name!r} is already claimed on this relay; "
            "its owner can release it with `quorus release-name`"))
    return {"name": req.name, "participant_key": key}


async def participant_token(request: Request, raw_key: str) -> dict:
    """``/v1/auth/token`` for ``quorus_pk_`` keys: a JWT for exactly the claimed name."""
    name = await asyncio.to_thread(_store(request).name_for, raw_key)
    if name is None:
        raise HTTPException(status_code=401, detail="Invalid participant key")
    token = create_jwt(sub=name, tenant_id=LEGACY_TENANT, tenant_slug=LEGACY_TENANT)
    return {"token": token, "expires_in": JWT_TTL_SECONDS}
