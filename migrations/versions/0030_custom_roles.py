"""Custom roles: the superuser's roles within fixed bounds (ADR 0039 step 3).

``roles`` holds each custom role: its template (which fixes the reach), names in Dutch and
English, the role claim value that links it to the identity provider, its permissions (space
separated) and a version. No personal data. It also joins the two heads 0027 (update steps)
and 0029 (maintenance), which both left main at the time. The downgrade drops the table:
whoever held a custom role then holds no role from it.

Revision ID: 0030
Revises: 0027, 0029
Create Date: 2026-10-28 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | tuple[str, ...] | None = ("0027", "0029")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``roles``."""
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template", sa.String(length=32), nullable=False),
        sa.Column("name_nl", sa.String(length=80), nullable=False),
        sa.Column("name_en", sa.String(length=80), nullable=False),
        sa.Column("claim_value", sa.String(length=128), nullable=False),
        sa.Column("permissions", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("claim_value", name=op.f("uq_roles_claim_value")),
    )


def downgrade() -> None:
    """Drop ``roles``; custom roles and their links to claim values are gone."""
    op.drop_table("roles")
