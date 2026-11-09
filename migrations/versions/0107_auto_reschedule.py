"""Automatic proposals of the rebooking assistant (ADR 0057 step 4).

``groups`` gains ``reschedule_auto_send`` (off), ``reschedule_auto_delay_minutes`` (15, 5 to 120)
and ``reschedule_auto_max_bookings`` (10, 1 to 100). A new table ``shortfall_watches`` remembers
since when a slot has been short at the same counts, so the worker can wait out the delay
without a version number on the staffing. It holds counts and times only.

Revision ID: 0107
Revises: 0106
Create Date: 2026-11-09 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0107"
down_revision: str | None = "0106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the three group settings and the watch table."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "reschedule_auto_send", sa.Boolean(), server_default=sa.text("0"), nullable=False
            )
        )
        batch.add_column(
            sa.Column(
                "reschedule_auto_delay_minutes",
                sa.Integer(),
                server_default=sa.text("15"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "reschedule_auto_max_bookings",
                sa.Integer(),
                server_default=sa.text("10"),
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "reschedule_auto_delay", "reschedule_auto_delay_minutes BETWEEN 5 AND 120"
        )
        batch.create_check_constraint(
            "reschedule_auto_max", "reschedule_auto_max_bookings BETWEEN 1 AND 100"
        )
    op.create_table(
        "shortfall_watches",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("slot_start", UTCDateTime(), nullable=False),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("first_seen_at", UTCDateTime(), nullable=False),
        sa.Column("notified_at", UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_shortfall_watches_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "slot_start", name=op.f("pk_shortfall_watches")),
    )


def downgrade() -> None:
    """Drop the table and the settings; proposals already sent stay what they are."""
    op.drop_table("shortfall_watches")
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("reschedule_auto_max", type_="check")
        batch.drop_constraint("reschedule_auto_delay", type_="check")
        batch.drop_column("reschedule_auto_max_bookings")
        batch.drop_column("reschedule_auto_delay_minutes")
        batch.drop_column("reschedule_auto_send")
