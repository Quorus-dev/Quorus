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
CLAIM_CLAIM = "pk_claim"  # JWT claim holding the name's claim generation


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
    """Shared-secret holders claim a name once; the key is returned once. An
    invited member's own key claims ``<member>-<tool>`` as an agent they own
    (``quorus/auth/member_agents.py``)."""
    store = _store(request)
    if not auth.is_legacy:
        if auth.claims.get(CLAIM_CLAIM) and auth.sub:
            return await _claim_member_agent(req.name, request, auth, store)
        raise HTTPException(status_code=403, detail="only the relay's shared secret can claim")
    # Only claims that succeed count. A daemon whose name is already claimed
    # (its key lost to a reinstall or a localhost/127.0.0.1 alias) retried every
    # stream, and its 409s used up the hour's budget for every agent behind the
    # same IP: one stuck daemon locked the whole machine out (release check,
    # 2026-10-17). The caller already holds the admin secret; the cap is only a
    # guard against a runaway loop minting names.
    limits, bucket = request.app.state.rate_limit_service, f"claim-name:{get_real_ip(request)}"
    if await limits.is_rate_limited("global", bucket, CLAIMS_PER_HOUR, window=3600):
        raise HTTPException(status_code=429, detail="too many name claims; retry in an hour",
                            headers={"Retry-After": "3600"})
    key = await asyncio.to_thread(store.claim, req.name)
    if key is None:
        raise HTTPException(status_code=409, detail=(
            f"the name {req.name!r} is already claimed on this relay; "
            "its owner can release it with `quorus release-name`"))
    await limits.record("global", bucket, window=3600)
    return {"name": req.name, "participant_key": key}


async def _claim_member_agent(name: str, request: Request, auth: AuthContext,
                              store: ParticipantKeyStore) -> dict:
    from quorus.auth import member_agents
    from quorus.routes.join_redeem import _known_names

    limits, bucket = request.app.state.rate_limit_service, f"claim-agent:{auth.sub}"
    if await limits.is_rate_limited("global", bucket, CLAIMS_PER_HOUR, window=3600):
        raise HTTPException(status_code=429, detail="too many agent claims; retry in an hour",
                            headers={"Retry-After": "3600"})
    known = await _known_names(request)
    try:
        key = await asyncio.to_thread(member_agents.mint, store, auth.sub,
                                      auth.claims[CLAIM_CLAIM], name, known)
    except member_agents.MintError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    await limits.record("global", bucket, window=3600)
    return {"name": name, "participant_key": key, "owner": auth.sub}


async def participant_token(request: Request, raw_key: str) -> dict:
    """``/v1/auth/token`` for ``quorus_pk_`` keys: a JWT for exactly the claimed
    name, stamped with the claim's generation so a release retires it."""
    found = await asyncio.to_thread(_store(request).lookup, raw_key)
    if found is None:
        raise HTTPException(status_code=401, detail="Invalid participant key")
    name, claim_id = found
    token = create_jwt(sub=name, tenant_id=LEGACY_TENANT, tenant_slug=LEGACY_TENANT,
                       extra={CLAIM_CLAIM: claim_id})
    return {"token": token, "expires_in": JWT_TTL_SECONDS}


async def claim_still_current(request: Request, claims: dict) -> bool:
    """Checked on every request carrying a participant-key JWT."""
    claim_id = claims.get(CLAIM_CLAIM)
    store = getattr(request.app.state, "participant_keys", None)
    if store is None:
        try:
            store = _store(request)
        except HTTPException:
            return False
    return await asyncio.to_thread(store.is_current, claims.get("sub") or "", claim_id)
