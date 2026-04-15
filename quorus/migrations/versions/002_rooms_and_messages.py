"""Rooms, room_members, messages, webhooks, presence tables.

Revision ID: 002
Revises: 001
Create Date: 2026-04-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rooms",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_room_tenant_name"),
    )

    op.create_table(
        "room_members",
        sa.Column(
            "room_id",
            sa.String(36),
            sa.ForeignKey("rooms.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("participant_name", sa.Text(), primary_key=True),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("from_name", sa.Text(), nullable=False),
        sa.Column("to_name", sa.Text(), nullable=True),
        sa.Column(
            "room_id",
            sa.String(36),
            sa.ForeignKey("rooms.id"),
            nullable=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=False, server_default="chat"),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("chunk_group", sa.String(36), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("chunk_total", sa.Integer(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_messages_dm_fetch",
        "messages",
        ["tenant_id", "to_name", "delivered_at"],
    )
    op.create_index(
        "ix_messages_room_history",
        "messages",
        ["tenant_id", "room_id", "timestamp"],
    )

    op.create_table(
        "webhooks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("participant_name", sa.Text(), nullable=True),
        sa.Column(
            "room_id",
            sa.String(36),
            sa.ForeignKey("rooms.id"),
            nullable=True,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "presence",
        sa.Column("tenant_id", sa.String(36), primary_key=True),
        sa.Column("participant_name", sa.Text(), primary_key=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("room_id", sa.String(36), nullable=True),
        sa.Column("uptime_start", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("presence")
    op.drop_table("webhooks")
    op.drop_index("ix_messages_room_history", table_name="messages")
    op.drop_index("ix_messages_dm_fetch", table_name="messages")
    op.drop_table("messages")
    op.drop_table("room_members")
    op.drop_table("rooms")
