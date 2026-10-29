"""Diagnostic mode and retention per category (ADR 0040, part 4).

Creates ``diagnostic_modes`` (one row per group or whole organisation in diagnostic mode,
slugs only, no personal data) and indexes ``audit_events`` on category and time, which the
worker's pruning per category reads. The downgrade drops both; debug events stay until the
older code prunes them with the system events.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-29 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table and the index."""
    op.create_table(
        "diagnostic_modes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation", sa.String(length=64), nullable=False),
        sa.Column("group", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("until", sa.DateTime(), nullable=False),
        sa.Column("started_by", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation", "group", name="uq_diagnostic_modes_target"),
    )
    op.create_index("ix_audit_events_category_at", "audit_events", ["category", "at"], unique=False)


def downgrade() -> None:
    """Drop the index and the table."""
    op.drop_index("ix_audit_events_category_at", table_name="audit_events")
    op.drop_table("diagnostic_modes")
