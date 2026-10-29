"""Staff directory: ``staff`` and ``staff_memberships`` replace ``admin_profiles`` (ADR 0046).

Each admin's roles and the organisations and groups they reach are recorded at login, next to
the first, last and latest-seen times. Existing profiles are carried over with ``roles`` NULL
(not known yet), so their next login writes no change event. Names of admins only; no
personal data of citizens.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-28 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the staff tables, carry the profiles over and drop ``admin_profiles``."""
    op.create_table(
        "staff",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("roles", sa.String(length=255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_logout_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff")),
        sa.UniqueConstraint("subject", name=op.f("uq_staff_subject")),
    )
    op.create_table(
        "staff_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("staff_id", sa.Integer(), nullable=False),
        sa.Column("organisation", sa.String(length=64), nullable=False),
        sa.Column("group", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff.id"],
            name=op.f("fk_staff_memberships_staff_id_staff"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_memberships")),
    )
    op.create_index("ix_staff_memberships_staff_id", "staff_memberships", ["staff_id"])
    op.create_index("ix_staff_memberships_organisation", "staff_memberships", ["organisation"])
    op.create_index("ix_staff_memberships_group", "staff_memberships", ["group"])
    op.execute(
        "INSERT INTO staff (subject, display_name, roles, first_seen_at, last_login_at,"
        " last_seen_at) SELECT subject, display_name, NULL, last_login_at, last_login_at,"
        " last_login_at FROM admin_profiles"
    )
    op.drop_table("admin_profiles")


def downgrade() -> None:
    """Recreate ``admin_profiles`` from ``staff``; roles and memberships are dropped."""
    op.create_table(
        "admin_profiles",
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("subject", name=op.f("pk_admin_profiles")),
    )
    op.execute(
        "INSERT INTO admin_profiles (subject, display_name, last_login_at)"
        " SELECT subject, display_name, last_login_at FROM staff"
    )
    op.drop_index("ix_staff_memberships_group", table_name="staff_memberships")
    op.drop_index("ix_staff_memberships_organisation", table_name="staff_memberships")
    op.drop_index("ix_staff_memberships_staff_id", table_name="staff_memberships")
    op.drop_table("staff_memberships")
    op.drop_table("staff")
