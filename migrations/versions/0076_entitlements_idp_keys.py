"""Entitlements (ADR 0049): IdP values per organisation and group, grants on the staff record.

``organisations.idp_key`` and ``groups.idp_key`` are what the identity provider names an
organisation or group by in an entitlement value; both start as the slug, so the values a
PingFederate already sends stay valid. ``staff_memberships.role_key`` turns a membership into
one grant (role on organisation or group): existing rows become the cross product of the
person's roles of the matching reach, which is what those people hold today, so the directory
shows it truthfully. ``staff_refused_entitlements`` keeps the values of a person's last login
that gave no right, with their reason code. No personal data in any of it.

Revision ID: 0076
Revises: 0074
Create Date: 2026-10-31 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORGANISATION_ROLES = ("org_admin", "auditor")
"""Built-in roles of organisation reach at the time of this migration."""

GROUP_ROLES = ("group_admin", "agent")
"""Built-in roles of group reach at the time of this migration."""


def upgrade() -> None:
    """Add the columns and the table; IdP values from the slugs, grants from the roles."""
    with op.batch_alter_table("organisations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("idp_key", sa.String(length=64), server_default=sa.text("''"), nullable=False)
        )
    op.execute("UPDATE organisations SET idp_key = slug")
    with op.batch_alter_table("organisations", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_organisations_idp_key", ["idp_key"])
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("idp_key", sa.String(length=64), server_default=sa.text("''"), nullable=False)
        )
    op.execute("UPDATE groups SET idp_key = slug")
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_groups_organisation_idp_key", ["organisation_id", "idp_key"]
        )
    with op.batch_alter_table("staff_memberships", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "role_key", sa.String(length=40), server_default=sa.text("''"), nullable=False
            )
        )
    _split_memberships_by_role()
    op.create_table(
        "staff_refused_entitlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "staff_id",
            sa.Integer(),
            sa.ForeignKey("staff.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("organisation", sa.String(length=64), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_staff_refused_entitlements_staff_id", "staff_refused_entitlements", ["staff_id"]
    )
    op.create_index(
        "ix_staff_refused_entitlements_organisation",
        "staff_refused_entitlements",
        ["organisation"],
    )


def _split_memberships_by_role() -> None:
    """Give every existing membership row one role of the matching reach (cross product)."""
    connection = op.get_bind()
    staff = connection.execute(sa.text("SELECT id, roles FROM staff")).fetchall()
    for staff_id, roles in staff:
        held = [r for r in (roles or "").split(",") if r]
        organisation_roles = [r for r in held if r in ORGANISATION_ROLES]
        group_roles = [r for r in held if r in GROUP_ROLES]
        rows = connection.execute(
            sa.text('SELECT id, organisation, "group" FROM staff_memberships WHERE staff_id = :id'),
            {"id": staff_id},
        ).fetchall()
        for row_id, organisation, group in rows:
            matching = group_roles if group else organisation_roles
            connection.execute(
                sa.text("DELETE FROM staff_memberships WHERE id = :id"), {"id": row_id}
            )
            for role in matching:
                connection.execute(
                    sa.text(
                        "INSERT INTO staff_memberships (staff_id, role_key, organisation, "
                        '"group") VALUES (:staff_id, :role, :organisation, :group)'
                    ),
                    {
                        "staff_id": staff_id,
                        "role": role,
                        "organisation": organisation,
                        "group": group,
                    },
                )


def downgrade() -> None:
    """Drop the table and the columns; memberships fold back to one row per target."""
    op.drop_index(
        "ix_staff_refused_entitlements_organisation", table_name="staff_refused_entitlements"
    )
    op.drop_index("ix_staff_refused_entitlements_staff_id", table_name="staff_refused_entitlements")
    op.drop_table("staff_refused_entitlements")
    op.execute(
        "DELETE FROM staff_memberships WHERE id NOT IN ("
        'SELECT MIN(id) FROM staff_memberships GROUP BY staff_id, organisation, "group")'
    )
    with op.batch_alter_table("staff_memberships", schema=None) as batch_op:
        batch_op.drop_column("role_key")
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.drop_constraint("uq_groups_organisation_idp_key", type_="unique")
        batch_op.drop_column("idp_key")
    with op.batch_alter_table("organisations", schema=None) as batch_op:
        batch_op.drop_constraint("uq_organisations_idp_key", type_="unique")
        batch_op.drop_column("idp_key")
