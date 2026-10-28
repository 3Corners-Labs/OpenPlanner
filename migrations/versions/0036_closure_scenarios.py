"""Closure scenarios: prepared closure content per organisation or group.

ADR 0037 §4 (CL-4). ``closure_scenarios`` holds a name, the notices and headings per locale
(JSON), an optional contact block (JSON) and an optional image in ``closure_images``. A
scenario with ``group_id`` NULL belongs to the organisation. Organisation data only; no
personal data of citizens.

Revision ID: 0036
Revises: 0028
Create Date: 2026-10-29 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``closure_scenarios``."""
    op.create_table(
        "closure_scenarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("notices", sa.Text(), nullable=False),
        sa.Column("headings", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("contacts", sa.Text(), nullable=True),
        sa.Column("image_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_closure_scenarios_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_closure_scenarios_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["closure_images.id"],
            name=op.f("fk_closure_scenarios_image_id_closure_images"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_closure_scenarios")),
    )
    op.create_index(
        op.f("ix_closure_scenarios_organisation_id"),
        "closure_scenarios",
        ["organisation_id"],
    )


def downgrade() -> None:
    """Drop the scenarios and their images; running closures keep the copies they made."""
    op.execute(
        "DELETE FROM closure_images WHERE id IN "
        "(SELECT image_id FROM closure_scenarios WHERE image_id IS NOT NULL)"
    )
    op.drop_index(op.f("ix_closure_scenarios_organisation_id"), table_name="closure_scenarios")
    op.drop_table("closure_scenarios")
