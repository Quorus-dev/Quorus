"""Add reply_to column to messages table.

Revision ID: 003
Revises: 002
Create Date: 2026-04-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("reply_to", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_messages_reply_to",
        "messages",
        ["tenant_id", "room_id", "reply_to"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_reply_to", table_name="messages")
    op.drop_column("messages", "reply_to")
