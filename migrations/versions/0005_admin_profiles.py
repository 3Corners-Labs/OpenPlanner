"""Admin profiles: display name per identity-provider subject, for the audit log (ADR 0014).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23 21:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_profiles",
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("subject", name=op.f("pk_admin_profiles")),
    )


def downgrade() -> None:
    op.drop_table("admin_profiles")
