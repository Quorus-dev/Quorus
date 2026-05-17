"""Add next_attempt_at to message_outbox to fix retry-storm.

Revision ID: 012
Revises: 011
Create Date: 2026-05-16

Closes the 2026-05-16 launch-audit P0 correctness blocker A6.

Before this migration: _handle_failure in outbox_svc.py computed an
exponential-backoff ``delay`` value but NEVER stored it. The status
was reset to PENDING immediately and _claim_entries had no time
filter, so a failing entry was re-polled at every tick (default 1 Hz)
regardless of how long ago the failure occurred. Under a sustained
downstream 503 the worker would burn ~5 retries in ~5 seconds, then
mark the entry FAILED — instead of giving the downstream the 1s, 2s,
4s, 8s, 16s windows the RETRY_DELAYS table promises.

The fix is one new nullable timestamp column plus a covering index so
the new WHERE clause stays cheap as the outbox grows.
"""

import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # next_attempt_at: NULL means "ready to claim immediately" (the
    # default for newly-inserted entries; matches existing INSERT paths
    # that don't set this column). Non-NULL means "do not claim until
    # this timestamp." _handle_failure sets it to now() + RETRY_DELAYS[k].
    op.add_column(
        "message_outbox",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Drop the old poll index — the new one supersedes it. We keep both
    # for one release just in case the worker rolls back, then 013 can
    # drop the legacy one cleanly. For now, just add the new index and
    # leave ix_message_outbox_poll in place.
    op.create_index(
        "ix_message_outbox_poll_v2",
        "message_outbox",
        ["status", "next_attempt_at", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_message_outbox_poll_v2", table_name="message_outbox")
    op.drop_column("message_outbox", "next_attempt_at")
