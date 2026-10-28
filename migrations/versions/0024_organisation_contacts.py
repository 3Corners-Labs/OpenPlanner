"""Organisation contacts: the default contact block of an organisation (ADR 0037).

One JSON document per organisation with up to six contact options (phone, email, website,
chat, address) and optional headings per locale. Organisation data shown publicly on closure
and maintenance pages; no personal data of citizens.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-28 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``organisation_contacts``: one contact block per organisation."""
    op.create_table(
        "organisation_contacts",
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_organisation_contacts_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organisation_id", name=op.f("pk_organisation_contacts")),
    )


def downgrade() -> None:
    """Drop ``organisation_contacts``; the contact blocks are lost (organisation data only)."""
    op.drop_table("organisation_contacts")
