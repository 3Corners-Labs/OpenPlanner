"""Citizen change lead per group, replacing the working days of the own queue (ADR 0069).

``groups`` gains ``citizen_change_lead_minutes`` (default 60, 0 to 1440): how long before the
start the citizen can still move a booking; cancelling is possible until the start. Existing
groups get 60 minutes, or their dispatch lead when that is longer. ``queue_change_working_days``
is dropped; the downgrade restores it at 1, the earlier default.

Revises 0061 (own certificates). Open PRs also claim numbers, so recheck the head before
merging.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-30 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the lead, never below the dispatch lead, and drop the working days."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "citizen_change_lead_minutes",
                sa.Integer(),
                server_default=sa.text("60"),
                nullable=False,
            )
        )
    op.execute(
        "UPDATE groups SET citizen_change_lead_minutes = dispatch_lead_minutes "
        "WHERE dispatch_lead_minutes > 60"
    )
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("queue_change_days", type_="check")
        batch.drop_column("queue_change_working_days")
        batch.create_check_constraint(
            "citizen_lead", "citizen_change_lead_minutes BETWEEN 0 AND 1440"
        )


def downgrade() -> None:
    """Restore the working days at their old default and drop the lead."""
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("citizen_lead", type_="check")
        batch.drop_column("citizen_change_lead_minutes")
        batch.add_column(
            sa.Column(
                "queue_change_working_days",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "queue_change_days", "queue_change_working_days BETWEEN 1 AND 5"
        )
