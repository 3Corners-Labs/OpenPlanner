"""Audit detail: what a change touched (field names or keys such as rule.add), never values.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-23 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "detail", sa.String(length=255), server_default=sa.text("('')"), nullable=False
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_column("detail")
