"""Planner changes (ADR 0044 step 2): change lead per group, reason on a booking, acceptances.

``groups`` gains ``planner_change_min_lead_minutes`` (class D per group, 60 by default, never
below ``dispatch_lead_minutes``, checked in the domain). ``bookings`` gains ``change_reason``:
the code of the fixed reason a planner gave (``staffing``, ``technical``, ``unforeseen``),
never free text. ``shortfall_acceptances`` holds a planner's "leave as is" per slot with the
counts it was given for and the planner's subject identifier. No personal data of citizens.

Revision ID: 0039
Revises: 0037
Create Date: 2026-10-29 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0039"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two columns and create ``shortfall_acceptances``."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "planner_change_min_lead_minutes",
                sa.Integer(),
                server_default=sa.text("60"),
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "planner_lead", "planner_change_min_lead_minutes BETWEEN 0 AND 1440"
        )
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("change_reason", sa.String(length=16), nullable=True))
        batch.create_check_constraint(
            "change_reason",
            "change_reason IS NULL OR change_reason IN ('staffing', 'technical', 'unforeseen')",
        )
    op.create_table(
        "shortfall_acceptances",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("start", UTCDateTime(), nullable=False),
        sa.Column("agents", sa.Integer(), nullable=False),
        sa.Column("booked", sa.Integer(), nullable=False),
        sa.Column("accepted_at", UTCDateTime(), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_shortfall_acceptances_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "start", name=op.f("pk_shortfall_acceptances")),
    )


def downgrade() -> None:
    """Drop the table and the columns; bookings keep their status and times."""
    op.drop_table("shortfall_acceptances")
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint("change_reason", type_="check")
        batch.drop_column("change_reason")
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("planner_lead", type_="check")
        batch.drop_column("planner_change_min_lead_minutes")
