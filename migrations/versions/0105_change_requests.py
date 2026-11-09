"""Change requests of the machine API (ADR 0023 step 2).

One new table, ``change_requests``: a bot's request to cancel or move a booking, named by the
caller's phone number and appointment code, until the participant confirms it. No personal data.
Expand only (ADR 0116): the previous image neither reads nor writes it.

Revision ID: 0105
Revises: 0104
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0105"
down_revision: str | None = "0104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``change_requests``."""
    op.create_table(
        "change_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("target_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("booking_id", sa.Integer(), nullable=True),
        sa.Column("pinned_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_change_requests_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_change_requests_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_requests")),
        sa.UniqueConstraint("public_id", name=op.f("uq_change_requests_public_id")),
    )
    op.create_index("ix_change_requests_booking_id", "change_requests", ["booking_id"])
    op.create_index("ix_change_requests_group_id", "change_requests", ["group_id"])


def downgrade() -> None:
    """Drop ``change_requests``."""
    op.drop_index("ix_change_requests_group_id", table_name="change_requests")
    op.drop_index("ix_change_requests_booking_id", table_name="change_requests")
    op.drop_table("change_requests")
