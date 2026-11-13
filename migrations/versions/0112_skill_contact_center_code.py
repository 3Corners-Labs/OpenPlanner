"""A contact-center code per skill (ADR 0121 step 4b).

Expand only (ADR 0116): one nullable column ``skills.contact_center_code`` and a partial unique
index on ``(organisation_id, contact_center_code)``; the previous image neither reads nor writes
either, and a skill it saves keeps its code. Nothing is backfilled: no skill was mapped before.
The code is what the organisation's contact-center flow tests for a required skill; a dispatch
sends the codes of the skills a booking requires, and refuses when one has none.

Revision ID: 0112
Revises: 0111
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0112"
down_revision: str | None = "0111"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_skills_organisation_id_contact_center_code"


def upgrade() -> None:
    """Add the column and its index."""
    op.add_column("skills", sa.Column("contact_center_code", sa.String(length=64), nullable=True))
    op.create_index(
        INDEX,
        "skills",
        ["organisation_id", "contact_center_code"],
        unique=True,
        sqlite_where=sa.text("contact_center_code IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop the index and the column."""
    op.drop_index(INDEX, table_name="skills")
    with op.batch_alter_table("skills") as batch:
        batch.drop_column("contact_center_code")
