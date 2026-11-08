"""Admins' own overview layouts (ADR 0096 step 2).

``overview_layouts`` holds one row per staff member who arranged their overview: the widget
keys in order (comma-separated) and when they last changed. Keys only, no personal data; the
row is deleted with the staff record. Without a row the role's default layout applies.

Revises 0090 (usage reporting, ADR 0117). Number 0091 is claimed by #598 (ADR 0081); whichever
merges second rebases its ``down_revision`` onto the other, so recheck the head before merging.

Revision ID: 0092
Revises: 0090
Create Date: 2026-11-08 03:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0092"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``overview_layouts``."""
    op.create_table(
        "overview_layouts",
        sa.Column("staff_id", sa.Integer(), nullable=False),
        sa.Column("widget_keys", sa.String(length=500), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("staff_id"),
    )


def downgrade() -> None:
    """Drop ``overview_layouts``."""
    op.drop_table("overview_layouts")
