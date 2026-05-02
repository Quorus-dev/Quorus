"""Short, human-typable join codes for inviting teammates + agents.

Revision ID: 010
Revises: 009
Create Date: 2026-04-16

Motivation: sharing a 260-character `quorus://` base64 token over
iMessage/Slack is hostile — smart-quote autocorrect, line wrap on
paste, em-dash substitution for `--`. This table stores an 8-char
Crockford-alphabet code (e.g. `HX4K-M7ZP`) that the relay resolves
to the same payload the client-side token encodes. Co-exists with
the old token format (that path needs no server lookup and is kept
as a power-user alias).

`code` is canonical uppercase Crockford (no 0/O/1/I/L). `payload`
stores the join envelope as JSON (relay_url, room, api_key OR
secret). `expires_at` is checked on resolve. Codes are multi-use
within TTL so a team can share one code with humans + agents.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "join_codes",
        sa.Column("code", sa.String(16), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("room_id", sa.String(64), nullable=True),
        sa.Column("room_name", sa.String(256), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("join_codes")
