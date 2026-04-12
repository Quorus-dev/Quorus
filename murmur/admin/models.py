"""SQLAlchemy ORM models for Murmur's Postgres schema."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Admin tables (ported from v1, tunnel table dropped)
# ---------------------------------------------------------------------------


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, insert_default=_new_uuid)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False, insert_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), insert_default=_utcnow, nullable=False
    )

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = _new_uuid()
        if "display_name" not in kwargs:
            kwargs["display_name"] = ""
        if "created_at" not in kwargs:
            kwargs["created_at"] = _utcnow()
        super().__init__(**kwargs)

    participants: Mapped[list[Participant]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    rooms: Mapped[list[Room]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, insert_default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, insert_default="user")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), insert_default=_utcnow, nullable=False
    )

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = _new_uuid()
        if "role" not in kwargs:
            kwargs["role"] = "user"
        if "created_at" not in kwargs:
            kwargs["created_at"] = _utcnow()
        super().__init__(**kwargs)

    tenant: Mapped[Tenant] = relationship(back_populates="participants")
    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="participant", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_participant_tenant_name"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, insert_default=_new_uuid)
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_prefix: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), insert_default=_utcnow, nullable=False
    )

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = _new_uuid()
        if "created_at" not in kwargs:
            kwargs["created_at"] = _utcnow()
        super().__init__(**kwargs)

    participant: Mapped[Participant] = relationship(back_populates="api_keys")


# ---------------------------------------------------------------------------
# Productionize feature tables
# ---------------------------------------------------------------------------


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    tenant: Mapped[Tenant] = relationship(back_populates="rooms")
    members: Mapped[list[RoomMember]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_room_tenant_name"),
    )


class RoomMember(Base):
    __tablename__ = "room_members"

    room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    participant_name: Mapped[str] = mapped_column(Text, primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    room: Mapped[Room] = relationship(back_populates="members")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False
    )
    from_name: Mapped[str] = mapped_column(Text, nullable=False)
    to_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Note: FK dropped in migration 004 — rooms are in Redis, not Postgres
    room_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Denormalized room name (room metadata is in Redis, not Postgres)
    room_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(Text, nullable=False, default="chat")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    chunk_group: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_messages_dm_fetch", "tenant_id", "to_name", "delivered_at"),
        Index("ix_messages_room_history", "tenant_id", "room_id", "timestamp"),
    )


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False
    )
    participant_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    room_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("rooms.id"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class Presence(Base):
    __tablename__ = "presence"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    participant_name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    room_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    uptime_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
