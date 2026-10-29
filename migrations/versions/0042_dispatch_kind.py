"""Dispatch kind per group: contact center or OpenPlanner's own queue (ADR 0043).

``groups`` gains ``dispatch_kind`` (default ``contact_center``, so existing groups keep their
WxCC dispatch) and ``queue_change_working_days`` (own queue: how many working days before the
start a booking becomes read-only). Group settings only; no personal data.

Revision ID: 0042
Revises: 0045
Create Date: 2026-10-28 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add both columns with their checks."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "dispatch_kind",
                sa.String(length=32),
                server_default=sa.text("'contact_center'"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "queue_change_working_days",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "dispatch_kind", "dispatch_kind IN ('contact_center', 'openplanner_queue')"
        )
        batch.create_check_constraint(
            "queue_change_days", "queue_change_working_days BETWEEN 1 AND 5"
        )


def downgrade() -> None:
    """Drop both columns; every group dispatches to the contact center again."""
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("queue_change_days", type_="check")
        batch.drop_constraint("dispatch_kind", type_="check")
        batch.drop_column("queue_change_working_days")
        batch.drop_column("dispatch_kind")
