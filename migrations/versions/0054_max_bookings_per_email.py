"""Bookings per email address: the group setting ``max_bookings_per_email`` (ADR 0059).

``groups`` gains ``max_bookings_per_email`` (default 3, 1 to 10): how many future bookings
that hold a place one email address may have in the group. Existing groups get the default.

Revision ID: 0054
Revises: 0044
Create Date: 2026-10-30 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the column with its bounds."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "max_bookings_per_email",
                sa.Integer(),
                server_default=sa.text("3"),
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "max_bookings_per_email", "max_bookings_per_email BETWEEN 1 AND 10"
        )


def downgrade() -> None:
    """Drop the column; the limit is no longer enforced."""
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("max_bookings_per_email", type_="check")
        batch.drop_column("max_bookings_per_email")
