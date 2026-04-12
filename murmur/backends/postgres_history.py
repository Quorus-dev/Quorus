"""Postgres-backed room history backend.

Uses the existing `messages` table from migration 002. Room messages have
room_id set and to_name NULL. This backend queries that table for room
history operations.

Configuration:
    DATABASE_URL  postgresql+asyncpg:// connection string (required)
    MAX_ROOM_HISTORY  max messages retained per room (default: 2000)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from murmur.storage.postgres import get_db_session


class PostgresRoomHistoryBackend:
    """Append-only room history backed by Postgres messages table.

    Room messages are stored with room_id set and to_name NULL.
    The room name is looked up via JOIN with rooms table.
    """

    def __init__(self, max_history: int = 2000) -> None:
        self._max_history = max_history

    async def append(
        self, tenant_id: str, room_id: str, message: dict
    ) -> None:
        """Insert a room message into the messages table."""
        msg_id = message.get("id") or str(uuid.uuid4())
        from_name = message.get("from_name") or message.get("from", "")
        content = message.get("content", "")
        message_type = message.get("message_type", "chat")
        timestamp = message.get("timestamp") or datetime.now(timezone.utc).isoformat()
        reply_to = message.get("reply_to")

        async with get_db_session() as session:
            await session.execute(
                text("""
                    INSERT INTO messages (
                        id, tenant_id, from_name, to_name, room_id,
                        content, message_type, timestamp, reply_to
                    ) VALUES (
                        :id, :tenant_id, :from_name, NULL, :room_id,
                        :content, :message_type, :timestamp, :reply_to
                    )
                """),
                {
                    "id": msg_id,
                    "tenant_id": tenant_id,
                    "from_name": from_name,
                    "room_id": room_id,
                    "content": content,
                    "message_type": message_type,
                    "timestamp": timestamp,
                    "reply_to": reply_to,
                },
            )

            # Trim old messages if over max_history
            await self._trim_history(session, tenant_id, room_id)

    async def _trim_history(
        self, session, tenant_id: str, room_id: str
    ) -> None:
        """Delete oldest messages if room exceeds max_history."""
        # Count messages in room
        result = await session.execute(
            text("""
                SELECT COUNT(*) FROM messages
                WHERE tenant_id = :tenant_id AND room_id = :room_id
            """),
            {"tenant_id": tenant_id, "room_id": room_id},
        )
        count = result.scalar() or 0

        if count > self._max_history:
            # Delete oldest messages beyond limit
            excess = count - self._max_history
            await session.execute(
                text("""
                    DELETE FROM messages WHERE id IN (
                        SELECT id FROM messages
                        WHERE tenant_id = :tenant_id AND room_id = :room_id
                        ORDER BY timestamp ASC
                        LIMIT :excess
                    )
                """),
                {"tenant_id": tenant_id, "room_id": room_id, "excess": excess},
            )

    async def get_recent(
        self, tenant_id: str, room_id: str, limit: int
    ) -> list[dict]:
        """Get recent messages for a room."""
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT m.id, m.from_name, m.content, m.message_type,
                           m.timestamp, m.reply_to, r.name as room_name
                    FROM messages m
                    LEFT JOIN rooms r ON r.id = m.room_id
                    WHERE m.tenant_id = :tenant_id AND m.room_id = :room_id
                    ORDER BY m.timestamp DESC
                    LIMIT :limit
                """),
                {"tenant_id": tenant_id, "room_id": room_id, "limit": limit},
            )
            rows = result.fetchall()

        # Reverse to chronological order and convert to dicts
        messages = []
        for row in reversed(rows):
            msg = {
                "id": row.id,
                "from": row.from_name,
                "from_name": row.from_name,
                "room": row.room_name or "",
                "content": row.content,
                "message_type": row.message_type,
                "timestamp": row.timestamp,
            }
            if row.reply_to:
                msg["reply_to"] = row.reply_to
            messages.append(msg)
        return messages

    async def search(
        self,
        tenant_id: str,
        room_id: str,
        q: str = "",
        sender: str = "",
        message_type: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """Search messages in a room."""
        conditions = ["m.tenant_id = :tenant_id", "m.room_id = :room_id"]
        params: dict = {"tenant_id": tenant_id, "room_id": room_id, "limit": limit}

        if q:
            conditions.append("m.content ILIKE :q")
            params["q"] = f"%{q}%"
        if sender:
            conditions.append("m.from_name = :sender")
            params["sender"] = sender
        if message_type:
            conditions.append("m.message_type = :message_type")
            params["message_type"] = message_type

        where_clause = " AND ".join(conditions)

        async with get_db_session() as session:
            result = await session.execute(
                text(f"""
                    SELECT m.id, m.from_name, m.content, m.message_type,
                           m.timestamp, m.reply_to, r.name as room_name
                    FROM messages m
                    LEFT JOIN rooms r ON r.id = m.room_id
                    WHERE {where_clause}
                    ORDER BY m.timestamp DESC
                    LIMIT :limit
                """),
                params,
            )
            rows = result.fetchall()

        messages = []
        for row in reversed(rows):
            msg = {
                "id": row.id,
                "from": row.from_name,
                "from_name": row.from_name,
                "room": row.room_name or "",
                "content": row.content,
                "message_type": row.message_type,
                "timestamp": row.timestamp,
            }
            if row.reply_to:
                msg["reply_to"] = row.reply_to
            messages.append(msg)
        return messages

    async def get_by_id(
        self, tenant_id: str, room_id: str, message_id: str
    ) -> dict | None:
        """Get a specific message by ID."""
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT m.id, m.from_name, m.content, m.message_type,
                           m.timestamp, m.reply_to, r.name as room_name
                    FROM messages m
                    LEFT JOIN rooms r ON r.id = m.room_id
                    WHERE m.tenant_id = :tenant_id
                      AND m.room_id = :room_id
                      AND m.id = :message_id
                """),
                {
                    "tenant_id": tenant_id,
                    "room_id": room_id,
                    "message_id": message_id,
                },
            )
            row = result.fetchone()

        if not row:
            return None

        msg = {
            "id": row.id,
            "from": row.from_name,
            "from_name": row.from_name,
            "room": row.room_name or "",
            "content": row.content,
            "message_type": row.message_type,
            "timestamp": row.timestamp,
        }
        if row.reply_to:
            msg["reply_to"] = row.reply_to
        return msg

    async def get_thread(
        self, tenant_id: str, room_id: str, parent_id: str, limit: int = 50
    ) -> list[dict]:
        """Get a message thread (parent + replies)."""
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT m.id, m.from_name, m.content, m.message_type,
                           m.timestamp, m.reply_to, r.name as room_name
                    FROM messages m
                    LEFT JOIN rooms r ON r.id = m.room_id
                    WHERE m.tenant_id = :tenant_id
                      AND m.room_id = :room_id
                      AND (m.id = :parent_id OR m.reply_to = :parent_id)
                    ORDER BY m.timestamp ASC
                    LIMIT :limit
                """),
                {
                    "tenant_id": tenant_id,
                    "room_id": room_id,
                    "parent_id": parent_id,
                    "limit": limit,
                },
            )
            rows = result.fetchall()

        messages = []
        for row in rows:
            msg = {
                "id": row.id,
                "from": row.from_name,
                "from_name": row.from_name,
                "room": row.room_name or "",
                "content": row.content,
                "message_type": row.message_type,
                "timestamp": row.timestamp,
            }
            if row.reply_to:
                msg["reply_to"] = row.reply_to
            messages.append(msg)
        return messages

    async def delete(self, tenant_id: str, room_id: str) -> None:
        """Delete all messages for a room."""
        async with get_db_session() as session:
            await session.execute(
                text("""
                    DELETE FROM messages
                    WHERE tenant_id = :tenant_id AND room_id = :room_id
                """),
                {"tenant_id": tenant_id, "room_id": room_id},
            )

    async def rename_room_in_history(
        self, tenant_id: str, room_id: str, new_name: str
    ) -> None:
        """No-op: room name is fetched via JOIN, not stored in messages."""
        # The room name comes from the rooms table, so renaming the room
        # automatically updates history. Nothing to do here.
        pass
