"""The rollup's watermark and the backfill's record (ADR 0117 step 4).

``usage_rollup_state`` is one row: the id of the last fact the worker rolled into ``usage_hour``,
when it did, when the backfill of old bookings was completed, and the hour from which live
facts exist (the backfill never repeats what live code wrote). The rollup advances it in the same transaction as the counts, so a crash
neither double-counts nor loses a fact. ``usage_backfill`` holds one row per booking already
turned into facts by ``python -m app.cli usage-backfill`` (idempotence); it holds a booking id
and nothing else, and a booking row goes with its row. Both are expand-only and hold no
personal data.

Revises 0090 (usage reporting). Open PRs also claim numbers, so recheck the head before
merging.

Revision ID: 0091
Revises: 0090
Create Date: 2026-11-08 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``usage_rollup_state`` and ``usage_backfill``."""
    op.create_table(
        "usage_rollup_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.Column("rolled_at", sa.DateTime(), nullable=True),
        sa.Column("backfilled_at", sa.DateTime(), nullable=True),
        sa.Column("backfill_cutoff", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_rollup_state")),
    )
    op.create_table(
        "usage_backfill",
        sa.Column("booking_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_usage_backfill_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("booking_id", name=op.f("pk_usage_backfill")),
    )


def downgrade() -> None:
    """Drop both tables."""
    op.drop_table("usage_backfill")
    op.drop_table("usage_rollup_state")
