"""Drop foreign key on messages.room_id.

Room metadata is stored in Redis, not Postgres. The FK constraint would
cause INSERT failures when PostgresRoomHistoryBackend tries to store
messages for rooms that only exist in Redis.

Revision ID: 004
Revises: 003
Create Date: 2026-04-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the FK constraint on messages.room_id
    # Room IDs from Redis are treated as external identifiers
    op.drop_constraint(
        "messages_room_id_fkey",
        "messages",
        type_="foreignkey",
    )


def downgrade() -> None:
    # Re-add the FK constraint (only safe if all room_ids exist in rooms table)
    op.create_foreign_key(
        "messages_room_id_fkey",
        "messages",
        "rooms",
        ["room_id"],
        ["id"],
    )
