"""Add audit ledger for message tracing and delivery debugging.

Revision ID: 007
Revises: 006
Create Date: 2026-04-12

The audit ledger tracks the lifecycle of every message:
- created: message written to history
- queued: added to outbox for fan-out
- fanout_started: worker began processing
- fanout_completed: all recipients notified
- fanout_partial: some recipients failed
- fanout_failed: all recipients failed
- webhook_sent: webhook notification delivered
- webhook_failed: webhook notification failed

This enables:
- "What happened to message X?" queries
- Delivery dispute resolution
- Abuse investigation
- Incident response
- SLA monitoring
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("room_id", sa.String(64), nullable=True),
        sa.Column("room_name", sa.String(256), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False, index=True),
        sa.Column("actor", sa.String(64), nullable=True),  # who triggered the event
        sa.Column("target", sa.String(64), nullable=True),  # recipient for fan-out events
        sa.Column("details", postgresql.JSONB, nullable=True),  # extra context
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
    )

    # Composite index for message timeline queries
    op.create_index(
        "ix_audit_ledger_message_timeline",
        "audit_ledger",
        ["message_id", "created_at"],
    )

    # Index for tenant + time range queries (debugging, compliance)
    op.create_index(
        "ix_audit_ledger_tenant_time",
        "audit_ledger",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_ledger_tenant_time", table_name="audit_ledger")
    op.drop_index("ix_audit_ledger_message_timeline", table_name="audit_ledger")
    op.drop_table("audit_ledger")
