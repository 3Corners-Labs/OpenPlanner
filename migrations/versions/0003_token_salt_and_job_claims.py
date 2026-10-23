"""Token salt on bookings (ADR 0011) and claim time on jobs (stale-claim recovery).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 16:12:33.340833
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "management_token_salt",
                sa.String(length=64),
                server_default=sa.text("('')"),
                nullable=False,
            )
        )

    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("claimed_at")

    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_column("management_token_salt")
