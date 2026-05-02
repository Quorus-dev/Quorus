"""Add hash-chain receipt fields to audit ledger.

Revision ID: 011
Revises: 010
Create Date: 2026-05-02
"""

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_ledger", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_ledger", sa.Column("entry_hash", sa.String(64), nullable=True))
    op.add_column(
        "audit_ledger",
        sa.Column("receipt_signature", sa.String(128), nullable=True),
    )
    op.create_index("ix_audit_ledger_entry_hash", "audit_ledger", ["entry_hash"])


def downgrade() -> None:
    op.drop_index("ix_audit_ledger_entry_hash", table_name="audit_ledger")
    op.drop_column("audit_ledger", "receipt_signature")
    op.drop_column("audit_ledger", "entry_hash")
    op.drop_column("audit_ledger", "prev_hash")
