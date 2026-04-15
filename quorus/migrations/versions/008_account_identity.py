"""Add participant_id columns for immutable identity.

Revision ID: 008
Revises: 007
Create Date: 2026-04-12

This migration adds participant_id foreign keys to tables that currently
use name-based identity. The participant_id provides:
- Immutable identity (names can be changed, IDs cannot)
- Proper audit trails (track who did what by ID, not display name)
- Clean revocation (revoke by ID, not by guessing name variations)

All new columns are nullable for backward compatibility with existing data.
Code updates will populate these fields for new records. A backfill script
can be run separately to populate historical records.

Tables affected:
- room_members: add participant_id FK
- messages: add from_participant_id, to_participant_id FKs
- webhooks: add participant_id FK (nullable, already nullable participant_name)
- presence: add participant_id FK
"""

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # room_members: add participant_id (nullable for existing records)
    op.add_column(
        "room_members",
        sa.Column(
            "participant_id",
            sa.String(36),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_room_members_participant",
        "room_members",
        ["participant_id"],
    )

    # messages: add from_participant_id and to_participant_id
    op.add_column(
        "messages",
        sa.Column(
            "from_participant_id",
            sa.String(36),
            sa.ForeignKey("participants.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "to_participant_id",
            sa.String(36),
            sa.ForeignKey("participants.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_messages_from_participant",
        "messages",
        ["tenant_id", "from_participant_id"],
    )
    op.create_index(
        "ix_messages_to_participant",
        "messages",
        ["tenant_id", "to_participant_id"],
    )

    # webhooks: add participant_id
    op.add_column(
        "webhooks",
        sa.Column(
            "participant_id",
            sa.String(36),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_webhooks_participant",
        "webhooks",
        ["participant_id"],
    )

    # presence: add participant_id
    op.add_column(
        "presence",
        sa.Column(
            "participant_id",
            sa.String(36),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_presence_participant",
        "presence",
        ["participant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_presence_participant", table_name="presence")
    op.drop_column("presence", "participant_id")

    op.drop_index("ix_webhooks_participant", table_name="webhooks")
    op.drop_column("webhooks", "participant_id")

    op.drop_index("ix_messages_to_participant", table_name="messages")
    op.drop_index("ix_messages_from_participant", table_name="messages")
    op.drop_column("messages", "to_participant_id")
    op.drop_column("messages", "from_participant_id")

    op.drop_index("ix_room_members_participant", table_name="room_members")
    op.drop_column("room_members", "participant_id")
