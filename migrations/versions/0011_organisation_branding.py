"""Organisation branding: display name, accent colour and logo (WP3b, open point O5).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-25 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("organisations") as batch:
        batch.add_column(sa.Column("display_name", sa.String(length=200), nullable=True))
        batch.add_column(
            sa.Column(
                "accent_color",
                sa.String(length=7),
                server_default=sa.text("'#01689B'"),
                nullable=False,
            )
        )
        batch.add_column(sa.Column("logo_media_type", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("logo_digest", sa.String(length=16), nullable=True))
    op.create_table(
        "organisation_logos",
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_organisation_logos_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organisation_id", name=op.f("pk_organisation_logos")),
    )


def downgrade() -> None:
    op.drop_table("organisation_logos")
    with op.batch_alter_table("organisations") as batch:
        batch.drop_column("logo_digest")
        batch.drop_column("logo_media_type")
        batch.drop_column("accent_color")
        batch.drop_column("display_name")
