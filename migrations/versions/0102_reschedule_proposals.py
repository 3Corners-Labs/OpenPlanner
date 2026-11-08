"""Held proposals of a new time (ADR 0057 step 2).

One new table, ``reschedule_proposals``: a proposed slot for a booking that awaits a new time,
holding one place there until the participant answers or the deadline passes. Expand only (ADR
0116): the previous image neither reads nor writes it, and its counts simply do not see a held
place, as before this step.

Revision ID: 0102
Revises: 0101
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0102"
down_revision: str | None = "0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``reschedule_proposals``."""
    op.create_table(
        "reschedule_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirm_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('confirmed', 'chosen', 'cancelled', 'expired', 'withdrawn')",
            name=op.f("ck_reschedule_proposals_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_reschedule_proposals_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"],
            ["slots.id"],
            name=op.f("fk_reschedule_proposals_slot_id_slots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reschedule_proposals")),
    )
    op.create_index("ix_reschedule_proposals_booking_id", "reschedule_proposals", ["booking_id"])
    op.create_index("ix_reschedule_proposals_slot_id", "reschedule_proposals", ["slot_id"])


def downgrade() -> None:
    """Drop ``reschedule_proposals``."""
    op.drop_index("ix_reschedule_proposals_slot_id", table_name="reschedule_proposals")
    op.drop_index("ix_reschedule_proposals_booking_id", table_name="reschedule_proposals")
    op.drop_table("reschedule_proposals")
