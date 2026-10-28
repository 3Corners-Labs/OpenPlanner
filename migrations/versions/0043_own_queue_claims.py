"""Own queue: claims on bookings and the queue settings of a group (ADR 0043).

``bookings`` gains ``claimed_at`` and ``claimed_by`` (the agent's pseudonym, removed by the
purge with the citizen's data); ``groups`` gains ``queue_visible_from_minutes`` (default 15)
and ``queue_free_pick_allowed`` (default off).

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-29 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the claim columns and the two queue settings."""
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("claimed_by", sa.String(length=64), nullable=True))
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "queue_visible_from_minutes",
                sa.Integer(),
                server_default=sa.text("15"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "queue_free_pick_allowed",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "queue_visible_from", "queue_visible_from_minutes BETWEEN 0 AND 120"
        )


def downgrade() -> None:
    """Drop the columns; open claims are forgotten."""
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("queue_visible_from", type_="check")
        batch.drop_column("queue_free_pick_allowed")
        batch.drop_column("queue_visible_from_minutes")
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("claimed_by")
        batch.drop_column("claimed_at")
