"""Composite index on audit_ledger for admin metrics dashboards.

Revision ID: 009
Revises: 008
Create Date: 2026-04-15

Supports the admin metrics queries which filter by event_type and sort
by created_at:

    SELECT ... FROM audit_ledger
    WHERE event_type = 'message_created'
      AND created_at >= ...
    ORDER BY created_at DESC

The existing per-column indexes on tenant_id, message_id, event_type,
and created_at each help some queries, but the dashboard's hot path
uses event_type + created_at together. A composite index keeps those
queries cheap even as the audit table grows.

Idempotent: uses IF NOT EXISTS so reruns on a seeded database succeed.
"""

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_event_created "
        "ON audit_ledger (event_type, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_event_created")
