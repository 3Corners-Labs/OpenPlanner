"""Maintenance mode of the whole installation (ADR 0038, MM-1).

``maintenance_state`` holds at most one row: whether maintenance is on, since when, who
switched it on (a subject identifier or a host actor), the expected end and a reason code.
No personal data. The downgrade drops the table; maintenance is then off, which is what a
version without this feature means.

Revision ID: 0029
Revises: 0026
Create Date: 2026-10-28 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``maintenance_state``."""
    op.create_table(
        "maintenance_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("since", sa.DateTime(), nullable=True),
        sa.Column(
            "switched_by", sa.String(length=255), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("expected_end", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.String(length=16), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_maintenance_state_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_state")),
    )


def downgrade() -> None:
    """Drop ``maintenance_state``."""
    op.drop_table("maintenance_state")
