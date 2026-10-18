"""Reserve a name before the shared-secret holder acts as it (2026-10-18).

On a shared-secret relay, rooms grant access by name, and an invite redeem
claims a name. Checking "is this name known?" before the redeem left a window:
create a room as "arav" between that check and the claim, and the invitee
held arav's name and read the room (codex, review of 8e6fa5c2). Every grant
the secret holder makes for a name now reserves it first, under the same
SQLite write lock as the redeem, so one of the two always sees the other.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import HTTPException, Request

from quorus.auth.middleware import AuthContext

LEGACY_TENANT = "_legacy"


async def reserve_names(request: Request, auth: AuthContext, tenant: str,
                        *names: str) -> None:
    """Raise 409 if an invited member already holds one of *names*."""
    if tenant != LEGACY_TENANT or os.environ.get("DATABASE_URL"):
        return
    # A participant acting as itself (its own key) needs no reservation.
    wanted = [n for n in names if n and n != auth.sub]
    if not wanted:
        return
    from quorus.auth import invite_codes
    from quorus.auth.participant_routes import _store

    refused = await asyncio.to_thread(invite_codes.reserve, _store(request), wanted)
    if refused:
        raise HTTPException(status_code=409, detail=(
            f"the name {refused!r} belongs to an invited member of this relay; "
            "pick another name"))


async def reserve_recipients(request: Request, tenant: str, *names: str) -> None:
    """Reserve a message's recipients before delivery. Existing members,
    invitees included, receive as usual; a name nobody holds is reserved, so
    no later invite redeem can claim it and read what was sent to it."""
    if tenant != LEGACY_TENANT or os.environ.get("DATABASE_URL"):
        return
    wanted = [n for n in names if n]
    if not wanted:
        return
    from quorus.auth import invite_codes
    from quorus.auth.participant_routes import _store

    await asyncio.to_thread(invite_codes.reserve, _store(request), wanted,
                            deliver_to_claimed=True)
