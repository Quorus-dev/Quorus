"""``POST /v1/join/redeem``: turn an invite code into the joiner's own key.

Shared-secret relays only (docs/weekend/invite-roles.md, step 1). The code is
the bearer proof; redeeming spends one use, claims the name as a human, adds it
to the invite's room with the invite's role, and returns the participant key
once. The joiner never receives ``RELAY_SECRET``.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from quorus.auth import invite_codes
from quorus.config import get_real_ip
from quorus.routes.helpers import _validate_name

router = APIRouter(prefix="/v1/join", tags=["join-codes"])
LEGACY_TENANT = "_legacy"
REDEEMS_PER_HOUR = 10
MAX_ROOM_MEMBERS = int(os.environ.get("MAX_ROOM_MEMBERS", "50"))


class RedeemRequest(BaseModel):
    code: str = Field(..., min_length=10, max_length=80)
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _validate_name(v)


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
    try:
        key, invite = await asyncio.to_thread(invite_codes.redeem, store, req.code, req.name)
    except invite_codes.InviteError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    rooms = request.app.state.room_service
    try:
        rid, _ = await rooms.get(LEGACY_TENANT, invite.room_id)
        await rooms.join(LEGACY_TENANT, rid, req.name, invite.role, MAX_ROOM_MEMBERS)
        await request.app.state.backends.participants.add(LEGACY_TENANT, req.name)
    except Exception:
        # The room went away or is full: give the use back and free the name.
        await asyncio.to_thread(invite_codes.undo_redeem, store, invite.id, req.name)
        raise HTTPException(status_code=409, detail="couldn't join the room; ask for a "
                                                    "new invite") from None
    return {"participant_key": key, "name": req.name, "room": invite.room_name,
            "role": invite.role}
