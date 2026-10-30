"""Release listing (ADR 0067): the releases and candidates the last check saw, with notes.

``release_listing`` is replaced on every successful check; the updates page lists the versions
newer than the running one from it, with the generated notes (PR titles and numbers) as plain
text. No personal data.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-30 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table."""
    op.create_table(
        "release_listing",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(length=20), nullable=False, unique=True),
        sa.Column("release_url", sa.String(length=300), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    """Drop the table; the page then offers the latest version only, as before."""
    op.drop_table("release_listing")
