"""PostgresQueueBackend — Postgres-backed QueueBackend implementation."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update

from murmur.admin.models import Message
from murmur.storage.postgres import get_db_session


class PostgresQueueBackend:
    """Queue backend backed by the Postgres messages table.

    Each method opens its own session via ``get_db_session()`` which handles
    commit/rollback automatically.
    """

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _msg_dict_to_orm(tenant_id: str, to_name: str, message: dict) -> Message:
        """Map an incoming message dict to a Message ORM instance."""
        return Message(
            id=message["id"],
            tenant_id=tenant_id,
            from_name=message["from_name"],
            to_name=to_name,
            content=message["content"],
            timestamp=message.get("timestamp", datetime.now(timezone.utc)),
            room_id=message.get("room"),
            chunk_group=message.get("chunk_group"),
            chunk_index=message.get("chunk_index"),
            chunk_total=message.get("chunk_total"),
            message_type=message.get("message_type", "chat"),
        )

    @staticmethod
    def _orm_to_dict(msg: Message) -> dict:
        """Convert a Message ORM instance back to a plain dict."""
        return {
            "id": msg.id,
            "from_name": msg.from_name,
            "to": msg.to_name,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
            "room": msg.room_id,
            "chunk_group": msg.chunk_group,
            "chunk_index": msg.chunk_index,
            "chunk_total": msg.chunk_total,
            "message_type": msg.message_type,
        }

    # -- protocol methods -----------------------------------------------------

    async def enqueue(self, tenant_id: str, to_name: str, message: dict) -> None:
        """INSERT a single message into the messages table."""
        async with get_db_session() as session:
            row = self._msg_dict_to_orm(tenant_id, to_name, message)
            session.add(row)

    async def dequeue_all(self, tenant_id: str, to_name: str) -> list[dict]:
        """SELECT undelivered messages, mark them delivered, return as dicts."""
        async with get_db_session() as session:
            stmt = (
                select(Message)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.to_name == to_name,
                    Message.delivered_at.is_(None),
                )
                .order_by(Message.timestamp.asc())
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            if not rows:
                return []

            ids = [r.id for r in rows]
            now = datetime.now(timezone.utc)
            await session.execute(
                update(Message)
                .where(Message.id.in_(ids))
                .values(delivered_at=now)
            )

            return [self._orm_to_dict(r) for r in rows]

    async def peek(self, tenant_id: str, to_name: str) -> int:
        """COUNT undelivered messages for a recipient."""
        async with get_db_session() as session:
            stmt = (
                select(func.count())
                .select_from(Message)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.to_name == to_name,
                    Message.delivered_at.is_(None),
                )
            )
            result = await session.execute(stmt)
            return result.scalar_one()

    async def enqueue_batch(
        self, tenant_id: str, to_name: str, messages: list[dict]
    ) -> None:
        """INSERT multiple messages in a single transaction."""
        async with get_db_session() as session:
            for message in messages:
                row = self._msg_dict_to_orm(tenant_id, to_name, message)
                session.add(row)
