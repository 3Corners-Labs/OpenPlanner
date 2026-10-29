"""Organisation values of the class D settings (ADR 0042 step 5).

``organisation_settings`` holds one JSON value per organisation and class D setting that an
organisation admin set in the portal: the time format, the admin portal's time zone, and the
staff, shortfall and rebook periods. Without a row the platform value applies. The rows go
with their organisation. No personal data. The downgrade drops the table; every organisation
then follows the platform value again.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-29 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``organisation_settings``."""
    op.create_table(
        "organisation_settings",
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_organisation_settings_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organisation_id", "key", name=op.f("pk_organisation_settings")),
    )


def downgrade() -> None:
    """Drop ``organisation_settings``."""
    op.drop_table("organisation_settings")
