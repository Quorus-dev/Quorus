"""``POST /v1/join/redeem``: turn an invite code into the joiner's own key.
``GET /v1/join/invites`` and ``POST /v1/join/invites/{id}/revoke``: the
relay owner's list of invites (never their codes) and the way to kill one,
which also removes from its room everyone who joined with it.

Shared-secret relays only (docs/design/invite-roles.md, step 1). The code is
the bearer proof; redeeming spends one use, claims the name as a human, adds it
to the invite's room with the invite's role, and returns the participant key
once. The joiner never receives ``RELAY_SECRET``.
"""

from __future__ import annotations

import asyncio
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from quorus.auth import invite_codes
from quorus.auth.middleware import AuthContext, verify_auth
from quorus.config import get_real_ip
from quorus.routes.helpers import _validate_name
from quorus.routes.room_auth import drop_owned_agents

router = APIRouter(prefix="/v1/join", tags=["join-codes"])
LEGACY_TENANT = "_legacy"
REDEEMS_PER_HOUR = 10
MAX_ROOM_MEMBERS = int(os.environ.get("MAX_ROOM_MEMBERS", "50"))
_INVITE_ID = re.compile(r"inv_[0-9a-f]{12}")


class RedeemRequest(BaseModel):
    code: str = Field(..., min_length=10, max_length=80)
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _validate_name(v)


async def _known_names(request: Request) -> set[str]:
    """Every name this relay already answers to: registered participants and
    members of any room. On a shared-secret relay these rarely hold a claim,
    yet rooms grant access by name, so none of them may be redeemed into."""
    names = set(await request.app.state.backends.participants.list_all(LEGACY_TENANT))
    for room in await request.app.state.room_service.list_all(LEGACY_TENANT):
        names.update(room.get("members") or ())
    return names


@router.post("/redeem")
async def redeem(req: RedeemRequest, request: Request) -> dict:
    if os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=404, detail="this relay issues API keys")
    rate = request.app.state.rate_limit_service
    if not await rate.check_with_limit("public", f"join-redeem:{get_real_ip(request)}",
                                       REDEEMS_PER_HOUR, window=3600):
        raise HTTPException(status_code=429, detail="too many join attempts; try later")
    from quorus.auth.participant_routes import _store

    store = _store(request)
    rooms = request.app.state.room_service
    known = await _known_names(request)
    try:
        key, claim_id, invite = await asyncio.to_thread(
            invite_codes.redeem, store, req.code, req.name, known)
    except invite_codes.InviteError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    try:
        rid, _ = await rooms.get(LEGACY_TENANT, invite.room_id)
        await rooms.join(LEGACY_TENANT, rid, req.name, invite.role, MAX_ROOM_MEMBERS)
        await request.app.state.backends.participants.add(LEGACY_TENANT, req.name)
    except Exception:
        # The room went away or is full: give the use back and free the name.
        await asyncio.to_thread(invite_codes.undo_redeem, store, invite.id, req.name,
                                claim_id)
        raise HTTPException(status_code=409, detail="couldn't join the room; ask for a "
                                                    "new invite") from None
    # A revoke can land between spending the use and joining the room (codex,
    # review of 79d70198): it finds no member to remove, then the join lands. So
    # check again now. Revoke sets the flag before it reads the members, so either
    # it removes this member or this check sees the flag.
    if await asyncio.to_thread(invite_codes.is_revoked, store, invite.id):
        await rooms.leave(LEGACY_TENANT, rid, req.name)
        await asyncio.to_thread(invite_codes.undo_redeem, store, invite.id, req.name,
                                claim_id)
        raise HTTPException(status_code=403, detail="this invite was revoked")
    return {"participant_key": key, "name": req.name, "room": invite.room_name,
            "role": invite.role}


def _owner_only(auth: AuthContext) -> None:
    """Invites live on shared-secret relays, and only the secret's holder (the
    relay owner) may list or revoke them: not a participant key, not an agent."""
    if os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=404, detail="this relay issues API keys")
    if not auth.is_legacy:
        raise HTTPException(status_code=403, detail="only the relay owner manages invites")


@router.get("/invites")
async def list_invites(request: Request, auth: AuthContext = Depends(verify_auth)) -> list:
    _owner_only(auth)
    from quorus.auth.participant_routes import _store

    return await asyncio.to_thread(invite_codes.list_invites, _store(request))


@router.post("/invites/{invite_id}/revoke")
async def revoke_invite(invite_id: str, request: Request,
                        auth: AuthContext = Depends(verify_auth)) -> dict:
    _owner_only(auth)
    if not _INVITE_ID.fullmatch(invite_id):
        raise HTTPException(status_code=400, detail="invalid invite id")
    rate = request.app.state.rate_limit_service
    if not await rate.check_with_limit("global", f"invite-revoke:{get_real_ip(request)}",
                                       60, window=60):
        raise HTTPException(status_code=429, detail="rate_limited")
    from quorus.auth.participant_routes import _store

    store = _store(request)
    if not await asyncio.to_thread(invite_codes.revoke, store, invite_id):
        raise HTTPException(status_code=404, detail="no such invite")
    # Revoking also removes who joined with it from its room. Room access is the
    # membership row, read on every request, so they're out on their next one.
    room_id, names = await asyncio.to_thread(invite_codes.joined_with, store, invite_id)
    rooms = request.app.state.room_service
    removed = []
    for name in names:
        try:
            rid, data = await rooms.get(LEGACY_TENANT, room_id)
        except HTTPException:
            break  # the room is gone, and its members with it
        if name in (data.get("members") or ()):
            await rooms.leave(LEGACY_TENANT, rid, name)
            await drop_owned_agents(request, LEGACY_TENANT, rid, name)
            removed.append(name)
    return {"id": invite_id, "revoked": True, "removed": removed}
