"""The admin session register (ADR 0098 step 1).

``admin_sessions`` holds one row per admin session: the ``sha256`` of the random reference the
cookie carries, the person, the claims of the login, when it started and was last seen, and
when and why it ended. Staff data only. Everyone logs in once more after this step.

Revises 0080 (log destinations, ADR 0085). Open PRs also claim numbers, so recheck the head
before merging.

Revision ID: 0081
Revises: 0080
Create Date: 2026-11-01 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``admin_sessions``."""
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("id_hash", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("claims", sa.Text(), nullable=False),
        sa.Column("login_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=16), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_sessions")),
        sa.UniqueConstraint("id_hash", name=op.f("uq_admin_sessions_id_hash")),
    )
    op.create_index(op.f("ix_admin_sessions_subject"), "admin_sessions", ["subject"])


def downgrade() -> None:
    """Drop ``admin_sessions``."""
    op.drop_index(op.f("ix_admin_sessions_subject"), table_name="admin_sessions")
    op.drop_table("admin_sessions")
