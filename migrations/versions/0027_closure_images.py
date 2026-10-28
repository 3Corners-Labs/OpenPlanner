"""Closure images: one image per closure with its text alternative.

ADR 0037 §3 (CL-3). ``closure_images`` holds the stripped image bytes, their type and digest
and the text alternative per locale (or ``decorative``); ``closures.image_id`` points at the
closure's image. Organisation data only; no personal data of citizens.

Revision ID: 0027
Revises: 0025
Create Date: 2026-10-28 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``closure_images`` and add ``closures.image_id``."""
    op.create_table(
        "closure_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("digest", sa.String(length=16), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("alts", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("decorative", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_closure_images")),
    )
    with op.batch_alter_table("closures") as batch:
        batch.add_column(sa.Column("image_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            batch.f("fk_closures_image_id_closure_images"),
            "closure_images",
            ["image_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """Drop the column and the table; closures keep their window, notices and contacts."""
    with op.batch_alter_table("closures") as batch:
        batch.drop_constraint(batch.f("fk_closures_image_id_closure_images"), type_="foreignkey")
        batch.drop_column("image_id")
    op.drop_table("closure_images")
