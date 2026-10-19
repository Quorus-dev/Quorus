"""Shared room authorization helpers.

Provides membership and permission checks for room-scoped routes.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from quorus.auth.middleware import AuthContext
from quorus.roles import OBSERVER, WRITER_ROLES  # one list for the relay and the daemon


async def require_room_member(
    request: Request, auth: AuthContext, tid: str, room_id: str,
) -> tuple[str, dict]:
    """Verify caller is a member of the room. Returns (room_id, room_data).

    Raises:
        HTTPException(404): if room not found
        HTTPException(403): if caller is not a member (unless legacy auth)
    """
    svc = request.app.state.room_service
    rid, room_data = await svc.get(tid, room_id)

    # Legacy auth (no JWT) skips membership check for backward compatibility
    if auth.is_legacy:
        return rid, room_data

    members = room_data.get("members", {})
    if auth.sub not in members:
        raise HTTPException(
            status_code=403,
            detail="Must be a room member to access this resource",
        )
    owner = await agent_owner(request, auth)
    if owner is not None and owner not in members:
        raise HTTPException(status_code=403, detail=OWNER_GONE)

    return rid, room_data


async def require_room_member_or_admin(
    request: Request, auth: AuthContext, tid: str, room_id: str,
) -> tuple[str, dict]:
    """Verify caller is a member or admin of the room. Returns (room_id, room_data).

    Admins can access any room in their tenant.

    Raises:
        HTTPException(404): if room not found
        HTTPException(403): if caller is not a member or admin (unless legacy auth)
    """
    svc = request.app.state.room_service
    rid, room_data = await svc.get(tid, room_id)

    # Legacy auth (no JWT) skips membership check for backward compatibility
    if auth.is_legacy:
        return rid, room_data

    # Check if user is admin (role from JWT)
    if auth.role == "admin":
        return rid, room_data

    members = room_data.get("members", {})
    if auth.sub not in members:
        raise HTTPException(
            status_code=403,
            detail="Must be a room member to access this resource",
        )
    owner = await agent_owner(request, auth)
    if owner is not None and owner not in members:
        raise HTTPException(status_code=403, detail=OWNER_GONE)

    return rid, room_data


# Observers (0.6.0): an invite can admit someone who may read one room but not
# change anything (codex's threat model). The role is the room's membership row,
# read fresh on every request, never a JWT claim, so a downgrade, kick or revoke
# applies to the next request. Unknown roles can't write either.
OBSERVER_DENIED = "observers can read this room but not change it"


async def _role_in(request: Request, tid: str, rid: str, name: str) -> str | None:
    members = await request.app.state.room_service.get_members(tid, rid)
    return (members or {}).get(name)


async def require_room_writer(
    request: Request, auth: AuthContext, tid: str, room_id: str,
) -> tuple[str, dict]:
    """A room member whose current role may change the room. Returns (room_id, room_data).

    The shared secret (the relay owner) and a tenant admin pass; everyone else
    needs a writer role on this room's membership row right now. A role in
    another room never counts here."""
    rid, room_data = await require_room_member(request, auth, tid, room_id)
    if auth.is_legacy or auth.role == "admin":
        return rid, room_data
    if await _role_in(request, tid, rid, auth.sub or "") not in WRITER_ROLES:
        raise HTTPException(status_code=403, detail=OBSERVER_DENIED)
    # An agent never writes above its owner: an observer's agent only reads.
    owner = await agent_owner(request, auth)
    if owner is not None and await _role_in(request, tid, rid, owner) not in WRITER_ROLES:
        raise HTTPException(status_code=403, detail=OBSERVER_DENIED)
    return rid, room_data


async def require_not_observer_only(request: Request, auth: AuthContext, tid: str) -> None:
    """For writes not tied to one room (DMs, new rooms, capabilities): refuse a
    caller who is in rooms but observes every one of them. An observer invite
    grants reading, never a way to reach agents from outside the room."""
    if auth.is_legacy or auth.role == "admin" or not auth.sub:
        return
    owner = await agent_owner(request, auth)
    if owner is not None:  # an agent may write only where its owner may
        await require_not_observer_only(request, AuthContext(sub=owner), tid)
    svc = request.app.state.room_service
    rooms = await svc.list_for_member(tid, auth.sub)
    if not rooms:
        if await _invited_as(request, auth.sub) == OBSERVER:
            raise HTTPException(status_code=403, detail=OBSERVER_DENIED)
        return
    if any((room.get("member_roles") or {}).get(auth.sub) in WRITER_ROLES for room in rooms):
        return
    raise HTTPException(status_code=403, detail=OBSERVER_DENIED)


async def _invited_as(request: Request, name: str) -> str | None:
    store = getattr(request.app.state, "participant_keys", None)
    if store is None:  # API-key relays keep no invite records here
        return None
    import asyncio

    from quorus.auth import invite_codes

    return await asyncio.to_thread(invite_codes.invited_as, store, name)


# Agents an invited member owns (quorus/auth/member_agents.py) act only in rooms
# their owner is in, never above the owner's role, and leave a room with them.
OWNER_GONE = "this agent's owner isn't a member of this room"


async def agent_owner(request: Request, auth: AuthContext) -> str | None:
    """The member who owns the calling agent, or None (not an owned agent)."""
    if auth.is_legacy or not auth.sub or not auth.claims.get("pk_claim"):
        return None
    store = getattr(request.app.state, "participant_keys", None)
    if store is None:
        return None
    import asyncio

    from quorus.auth.member_agents import owner_of

    return await asyncio.to_thread(owner_of, store, auth.sub)


async def drop_owned_agents(request: Request, tid: str, rid: str, owner: str) -> None:
    """After *owner* left or was removed from a room, every agent *owner* owns
    leaves it too, so no route that checks membership by name still lets them in."""
    store = getattr(request.app.state, "participant_keys", None)
    if store is None:
        return
    import asyncio

    from quorus.auth.member_agents import owner_of

    svc = request.app.state.room_service
    for member in list(await svc.get_members(tid, rid) or {}):
        if await asyncio.to_thread(owner_of, store, member) == owner:
            await svc.leave(tid, rid, member)
