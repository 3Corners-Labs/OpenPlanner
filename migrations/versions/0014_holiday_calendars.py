"""Holiday calendars maintained by the superuser, chosen per organisation and group (ADR 0024).

Existing groups start with ``holiday_source = 'inherit'`` and no organisation has chosen a
calendar yet, so nothing changes for citizens until an admin makes a choice.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-25 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "holiday_calendars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holiday_calendars")),
        sa.UniqueConstraint("slug", name=op.f("uq_holiday_calendars_slug")),
    )
    op.create_table(
        "holidays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["holiday_calendars.id"],
            name=op.f("fk_holidays_calendar_id_holiday_calendars"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holidays")),
        sa.UniqueConstraint("calendar_id", "day", name=op.f("uq_holidays_calendar_id_day")),
    )
    op.create_table(
        "organisation_holiday_calendars",
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["holiday_calendars.id"],
            name=op.f("fk_organisation_holiday_calendars_calendar_id_holiday_calendars"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_organisation_holiday_calendars_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "organisation_id", "calendar_id", name=op.f("pk_organisation_holiday_calendars")
        ),
    )
    op.create_table(
        "group_holiday_calendars",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["holiday_calendars.id"],
            name=op.f("fk_group_holiday_calendars_calendar_id_holiday_calendars"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_group_holiday_calendars_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "calendar_id", name=op.f("pk_group_holiday_calendars")),
    )
    op.create_table(
        "group_holiday_open_days",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_group_holiday_open_days_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "day", name=op.f("pk_group_holiday_open_days")),
    )
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "holiday_source",
                sa.String(length=16),
                server_default=sa.text("'inherit'"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            op.f("ck_groups_holiday_source"), "holiday_source IN ('inherit', 'own')"
        )


def downgrade() -> None:
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_groups_holiday_source"), type_="check")
        batch_op.drop_column("holiday_source")
    op.drop_table("group_holiday_open_days")
    op.drop_table("group_holiday_calendars")
    op.drop_table("organisation_holiday_calendars")
    op.drop_table("holidays")
    op.drop_table("holiday_calendars")
