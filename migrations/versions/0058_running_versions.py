"""Running versions (ADR 0067): the versions this installation ran, noted at start-up.

``running_versions`` gets one row each time the web process starts on another version or
commit than the last row; a change the updater did carries its request id, one from the
installer or by hand does not. No personal data.

Revision ID: 0058
Revises: 0056
Create Date: 2026-10-30 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table."""
    op.create_table(
        "running_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("commit", sa.String(length=40), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop the table; the history of versions is then only in the audit log."""
    op.drop_table("running_versions")
