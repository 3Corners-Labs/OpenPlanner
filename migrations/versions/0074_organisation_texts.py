"""Organisation texts (ADR 0074): an organisation's own wording of citizen pages and messages.

``organisation_texts`` holds one row per organisation, locale and catalogue key; a row replaces
the shipped text for every group of the organisation (a group's own text still wins). Plain text
only, validated against an allow-list of keys and placeholders before it is stored. No personal
data.

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-30 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table."""
    op.create_table(
        "organisation_texts",
        sa.Column(
            "organisation_id",
            sa.Integer(),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("locale", sa.String(length=8), primary_key=True),
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("text", sa.String(length=2000), nullable=False),
    )


def downgrade() -> None:
    """Drop the table; the shipped texts apply again."""
    op.drop_table("organisation_texts")
