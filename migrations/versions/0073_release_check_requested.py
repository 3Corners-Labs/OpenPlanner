"""A superuser's "Check now" on the updates page (ADR 0067).

``release_checks.requested_at`` marks that a superuser asked the worker to check for releases
ahead of the interval; the worker's next check clears it. No personal data.

Revision ID: 0073
Revises: 0071
Create Date: 2026-10-30 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable column."""
    with op.batch_alter_table("release_checks") as batch:
        batch.add_column(sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop the column; a pending "Check now" then waits for the interval."""
    with op.batch_alter_table("release_checks") as batch:
        batch.drop_column("requested_at")
