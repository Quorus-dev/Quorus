"""Add room_name column to messages.

Room metadata is stored in Redis, not Postgres. Denormalizing room_name
into the messages table allows history queries to return room names without
joining against a table that may not have the room.

Revision ID: 005
Revises: 004
Create Date: 2026-04-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("room_name", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "room_name")
