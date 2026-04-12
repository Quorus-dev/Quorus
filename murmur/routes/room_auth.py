"""Shared room authorization helpers.

Provides membership and permission checks for room-scoped routes.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from murmur.auth.middleware import AuthContext


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

    return rid, room_data
