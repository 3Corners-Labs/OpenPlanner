"""Daily booking counts that outlive the booking row (ADR 0102 step 4).

``booking_day_counts`` holds, per group, day and final status, how many bookings there were
and when the latest of them was purged. The worker merges a booking row into it
``booking_row_retention_days`` after the purge and then deletes the row. No personal data, so
the downgrade only drops the table (the counts are statistics and cannot be rebuilt).

Revises 0085 (staff status, ADR 0046). Open PRs also claim numbers, so recheck
the head before merging.

Revision ID: 0086
Revises: 0085
Create Date: 2026-11-02 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``booking_day_counts``."""
    op.create_table(
        "booking_day_counts",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("last_purged_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_booking_day_counts_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "day", "status", name=op.f("pk_booking_day_counts")),
    )


def downgrade() -> None:
    """Drop ``booking_day_counts``."""
    op.drop_table("booking_day_counts")
