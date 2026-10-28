"""Extended branding: theme tokens, header style and fonts per organisation (ADR 0041).

``theme_tokens`` and ``font_digests`` hold JSON written by the adapter from values the domain
validated; the fonts themselves (WOFF2) live in ``organisation_fonts``, one row per role.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("organisations") as batch:
        batch.add_column(
            sa.Column("theme_tokens", sa.Text(), server_default=sa.text("'{}'"), nullable=False)
        )
        batch.add_column(
            sa.Column(
                "header_style",
                sa.String(length=16),
                server_default=sa.text("'standard'"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column("font_digests", sa.Text(), server_default=sa.text("'{}'"), nullable=False)
        )
        batch.create_check_constraint(
            "header_style", "header_style IN ('standard', 'accent', 'logo')"
        )
    op.create_table(
        "organisation_fonts",
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "role IN ('text', 'text-bold', 'heading')", name=op.f("ck_organisation_fonts_role")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_organisation_fonts_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organisation_id", "role", name=op.f("pk_organisation_fonts")),
    )


def downgrade() -> None:
    op.drop_table("organisation_fonts")
    with op.batch_alter_table("organisations") as batch:
        batch.drop_constraint("header_style", type_="check")
        batch.drop_column("font_digests")
        batch.drop_column("header_style")
        batch.drop_column("theme_tokens")
