"""Add message outbox table for transactional fan-out.

Revision ID: 006
Revises: 005
Create Date: 2026-04-12

The outbox pattern ensures that message fan-out is transactional:
1. Room send writes to history + outbox in same transaction
2. Background worker processes outbox entries
3. Worker marks entries as processed (or failed with retry count)
4. Guarantees at-least-once delivery even if fan-out fails mid-way

States:
- pending: waiting to be processed
- processing: claimed by a worker
- completed: successfully fanned out
- failed: permanently failed after max retries (moves to DLQ)
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("room_id", sa.String(64), nullable=False),
        sa.Column("room_name", sa.String(256), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender", sa.String(64), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("message_type", sa.String(32), nullable=False, default="chat"),
        sa.Column("reply_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            default="pending",
            index=True,
        ),
        sa.Column("retry_count", sa.Integer, nullable=False, default=0),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Index for worker polling: find pending/failed items to process
    op.create_index(
        "ix_message_outbox_poll",
        "message_outbox",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    # Index for cleanup: find old completed entries to delete
    op.create_index(
        "ix_message_outbox_cleanup",
        "message_outbox",
        ["processed_at"],
        postgresql_where=sa.text("status = 'completed'"),
    )


def downgrade() -> None:
    op.drop_index("ix_message_outbox_cleanup", table_name="message_outbox")
    op.drop_index("ix_message_outbox_poll", table_name="message_outbox")
    op.drop_table("message_outbox")
