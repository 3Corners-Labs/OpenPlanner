"""Closure content: heading per locale, a copy of the contact block, group contact blocks.

ADR 0037 §2 (CL-1). ``closure_notices`` gains an optional ``heading`` next to each notice,
``closures`` gains ``contacts`` (the JSON copy of the contact block taken when the closure
started) and ``group_contacts`` holds a group's own block, which overrides the organisation
default. Organisation data only; no personal data of citizens.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-28 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the heading and contacts columns and create ``group_contacts``."""
    with op.batch_alter_table("closure_notices") as batch:
        batch.add_column(sa.Column("heading", sa.String(length=120), nullable=True))
    with op.batch_alter_table("closures") as batch:
        batch.add_column(sa.Column("contacts", sa.Text(), nullable=True))
    op.create_table(
        "group_contacts",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_group_contacts_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", name=op.f("pk_group_contacts")),
    )


def downgrade() -> None:
    """Drop the new table and columns; closures keep their window and notices (ADR 0024)."""
    op.drop_table("group_contacts")
    with op.batch_alter_table("closures") as batch:
        batch.drop_column("contacts")
    with op.batch_alter_table("closure_notices") as batch:
        batch.drop_column("heading")
