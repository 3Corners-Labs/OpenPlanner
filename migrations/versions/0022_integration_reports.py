"""Integration reports: the last ``webhooks-list`` and ``webhooks-health`` results.

Operator commands store what they found so the admin portal can show it read-only. A report
holds registration ids, target URLs without query strings, host account filters, statuses and
outcome codes: no secrets, tokens or personal data of citizens.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-25 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``integration_reports``: one JSON report per name, with when it was made."""
    op.create_table(
        "integration_reports",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_integration_reports")),
    )


def downgrade() -> None:
    """Drop ``integration_reports``; the next check writes them again."""
    op.drop_table("integration_reports")
