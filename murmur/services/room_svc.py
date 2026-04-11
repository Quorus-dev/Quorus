"""Room lifecycle service — create, get, join, leave, kick, destroy, rename."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import HTTPException

from murmur.backends.protocol import RoomBackend

logger = structlog.get_logger("murmur.services.room")


class RoomService:
    """Encapsulates room CRUD and membership management."""

    def __init__(self, backend: RoomBackend) -> None:
        self._backend = backend

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self, tenant_id: str, name: str, creator: str
    ) -> dict:
        """Create a new room.  Raises 409 if name is taken (atomic check)."""
        room_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        room_data = {
            "name": name,
            "created_by": creator,
            "tenant_id": tenant_id,
            "created_at": created_at,
            "members": {creator: "builder"},
        }
        created = await self._backend.create_if_name_available(
            tenant_id, room_id, room_data
        )
        if not created:
            raise HTTPException(
                status_code=409, detail="Room name already exists"
            )
        await self._backend.add_member(tenant_id, room_id, creator, "builder")

        logger.info("Room created: %s (%s) by %s", name, room_id, creator)
        return {
            "id": room_id,
            "name": name,
            "created_by": creator,
            "members": [creator],
            "member_roles": {creator: "builder"},
            "created_at": created_at,
        }

    # ------------------------------------------------------------------
    # Get / resolve
    # ------------------------------------------------------------------

    async def get(
        self, tenant_id: str, room_id_or_name: str
    ) -> tuple[str, dict]:
        """Resolve a room by ID or name.  Returns ``(room_id, room_data)``.

        Raises ``HTTPException(404)`` if not found.
        """
        # Try by ID first
        data = await self._backend.get(tenant_id, room_id_or_name)
        if data is not None:
            return room_id_or_name, data

        # Try by name
        result = await self._backend.get_by_name(tenant_id, room_id_or_name)
        if result is not None:
            return result  # (room_id, data)

        raise HTTPException(status_code=404, detail="Room not found")

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    async def list_all(self, tenant_id: str) -> list[dict]:
        """Return all rooms for a tenant."""
        pairs = await self._backend.list_all(tenant_id)
        return await self._format_room_list(tenant_id, pairs)

    async def list_for_member(
        self, tenant_id: str, member_name: str
    ) -> list[dict]:
        """Return rooms where member_name is a member (backend-optimized)."""
        pairs = await self._backend.list_by_member(tenant_id, member_name)
        return await self._format_room_list(tenant_id, pairs)

    async def _format_room_list(
        self, tenant_id: str, pairs: list[tuple[str, dict]]
    ) -> list[dict]:
        results = []
        for room_id, data in pairs:
            members = await self._backend.get_members(tenant_id, room_id)
            results.append(
                {
                    "id": room_id,
                    "name": data.get("name", ""),
                    "members": sorted(members.keys()),
                    "member_roles": members,
                    "created_at": data.get("created_at", ""),
                }
            )
        return results

    async def get_members(
        self, tenant_id: str, room_id: str
    ) -> dict[str, str]:
        """Return {name: role} for all members of a room."""
        return await self._backend.get_members(tenant_id, room_id)

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    async def join(
        self,
        tenant_id: str,
        room_id: str,
        participant: str,
        role: str,
        max_members: int,
    ) -> None:
        """Add *participant* to a room.  Raises 400 if at capacity (atomic)."""
        added = await self._backend.add_member_if_capacity(
            tenant_id, room_id, participant, role, max_members
        )
        if not added:
            raise HTTPException(
                status_code=400,
                detail=f"Room is at maximum capacity ({max_members} members)",
            )

    async def leave(
        self, tenant_id: str, room_id: str, participant: str
    ) -> None:
        """Remove *participant* from a room."""
        await self._backend.remove_member(tenant_id, room_id, participant)

    async def kick(
        self,
        tenant_id: str,
        room_id: str,
        participant: str,
        requester: str,
        requester_is_admin: bool,
    ) -> None:
        """Kick *participant* from a room.

        Only the room creator or an admin can kick.  Cannot kick the creator.
        """
        _, data = await self.get(tenant_id, room_id)
        creator = data.get("created_by", "")

        if not requester_is_admin and requester != creator:
            raise HTTPException(
                status_code=403,
                detail="Only the room creator or admin can kick members",
            )
        if participant == creator:
            raise HTTPException(
                status_code=400, detail="Cannot kick the room creator"
            )
        members = await self._backend.get_members(tenant_id, room_id)
        if participant not in members:
            raise HTTPException(
                status_code=404, detail="Participant not in room"
            )
        await self._backend.remove_member(tenant_id, room_id, participant)
        logger.info(
            "Kicked %s from room %s by %s",
            participant,
            data.get("name", room_id),
            requester,
        )

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------

    async def destroy(
        self,
        tenant_id: str,
        room_id: str,
        requester: str,
        requester_is_admin: bool,
    ) -> str:
        """Destroy a room.  Returns the room name.

        Only the room creator or an admin can destroy.
        """
        _, data = await self.get(tenant_id, room_id)
        creator = data.get("created_by", "")

        if not requester_is_admin and requester != creator:
            raise HTTPException(
                status_code=403,
                detail="Only the room creator or admin can destroy the room",
            )
        room_name = data.get("name", "")
        await self._backend.delete(tenant_id, room_id)
        logger.info(
            "Room %s (%s) destroyed by %s", room_name, room_id, requester
        )
        return room_name

    # ------------------------------------------------------------------
    # Rename
    # ------------------------------------------------------------------

    async def rename(
        self,
        tenant_id: str,
        room_id: str,
        new_name: str,
        requester: str,
        requester_is_admin: bool,
    ) -> tuple[str, str]:
        """Rename a room.  Returns ``(old_name, new_name)``.

        Only the room creator or an admin can rename.  Raises 409 if
        *new_name* is already taken.
        """
        _, data = await self.get(tenant_id, room_id)
        creator = data.get("created_by", "")

        if not requester_is_admin and requester != creator:
            raise HTTPException(
                status_code=403,
                detail="Only the room creator or admin can rename the room",
            )
        old_name = data.get("name", "")
        renamed = await self._backend.rename_if_available(
            tenant_id, room_id, new_name
        )
        if not renamed:
            raise HTTPException(
                status_code=409, detail="Room name already exists"
            )
        logger.info(
            "Room renamed: %s -> %s by %s", old_name, new_name, requester
        )
        return old_name, new_name
