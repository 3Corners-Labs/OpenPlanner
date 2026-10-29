"""Embedding on host websites: allowed origins per organisation, opt-in per group (ADR 0035).

``organisation_origins`` holds the websites (``https://www.gemeente.nl``) on which the
organisation's groups may be embedded, in the order the admin entered them. Organisation
data, no personal data. ``groups.embedding_enabled`` is off for every existing group, so
nothing becomes embeddable by this migration.

Revision ID: 0037
Revises: 0034
Create Date: 2026-10-28 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``organisation_origins`` and add ``groups.embedding_enabled`` (off)."""
    op.create_table(
        "organisation_origins",
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_organisation_origins_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organisation_id", "origin", name=op.f("pk_organisation_origins")),
    )
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "embedding_enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False
            )
        )


def downgrade() -> None:
    """Drop both; every group is standalone-only again and the origin lists are lost."""
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.drop_column("embedding_enabled")
    op.drop_table("organisation_origins")
