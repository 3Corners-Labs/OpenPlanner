"""Booking schema: organisations, groups, schedules, reminder rules, slots, bookings, jobs.

Datetime columns hold naive UTC; ``app.adapters.db.types.UTCDateTime`` converts at the
boundary. Personal data lives only in ``bookings`` (nullable for the purge).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23 15:49:44.545122
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organisations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "default_locale", sa.String(length=16), server_default=sa.text("'nl'"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organisations")),
        sa.UniqueConstraint("slug", name=op.f("uq_organisations_slug")),
    )
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("slot_minutes", sa.Integer(), nullable=False),
        sa.Column("min_notice_minutes", sa.Integer(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("phone_mode", sa.String(length=16), nullable=False),
        sa.Column("wxcc_queue_id", sa.String(length=128), nullable=True),
        sa.Column(
            "dispatch_lead_minutes", sa.Integer(), server_default=sa.text("5"), nullable=False
        ),
        sa.CheckConstraint(
            "dispatch_lead_minutes BETWEEN 0 AND 60", name=op.f("ck_groups_dispatch_lead")
        ),
        sa.CheckConstraint(
            "phone_mode IN ('hidden', 'optional', 'required')", name=op.f("ck_groups_phone_mode")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_groups_organisation_id_organisations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_groups")),
        sa.UniqueConstraint("slug", name=op.f("uq_groups_slug")),
    )
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_groups_organisation_id"), ["organisation_id"], unique=False
        )

    op.create_table(
        "reminder_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("minutes_before", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "channel IN ('email', 'sms', 'whatsapp')", name=op.f("ck_reminder_rules_channel")
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_reminder_rules_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminder_rules")),
        sa.UniqueConstraint(
            "group_id",
            "channel",
            "minutes_before",
            name=op.f("uq_reminder_rules_group_id_channel_minutes_before"),
        ),
    )
    op.create_table(
        "schedule_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("opens", sa.Time(), nullable=True),
        sa.Column("closes", sa.Time(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(opens IS NULL AND closes IS NULL AND capacity IS NULL) "
            "OR (opens < closes AND capacity >= 1)",
            name=op.f("ck_schedule_overrides_window_or_closed"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_schedule_overrides_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_overrides")),
    )
    with op.batch_alter_table("schedule_overrides", schema=None) as batch_op:
        batch_op.create_index("ix_schedule_overrides_group_day", ["group_id", "day"], unique=False)

    op.create_table(
        "schedule_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("opens", sa.Time(), nullable=False),
        sa.Column("closes", sa.Time(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.CheckConstraint("capacity >= 1", name=op.f("ck_schedule_rules_capacity")),
        sa.CheckConstraint("opens < closes", name=op.f("ck_schedule_rules_window")),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=op.f("ck_schedule_rules_weekday")),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_schedule_rules_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_rules")),
    )
    with op.batch_alter_table("schedule_rules", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_schedule_rules_group_id"), ["group_id"], unique=False)

    op.create_table(
        "slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("start", sa.DateTime(), nullable=False),
        sa.Column("end", sa.DateTime(), nullable=False),
        sa.Column("capacity_snapshot", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"], ["groups.id"], name=op.f("fk_slots_group_id_groups"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slots")),
        sa.UniqueConstraint("group_id", "start", name=op.f("uq_slots_group_id_start")),
    )
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone", sa.String(length=16), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("consents", sa.String(length=64), server_default=sa.text("('')"), nullable=False),
        sa.Column("management_token_hash", sa.String(length=64), nullable=False),
        sa.Column("guest_join_url", sa.String(length=2048), nullable=True),
        sa.Column("host_join_url", sa.String(length=2048), nullable=True),
        sa.Column("video_room_id", sa.String(length=255), nullable=True),
        sa.Column("wxcc_task_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("pii_purged_at", sa.DateTime(), nullable=True),
        sa.Column("rescheduled_from_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('confirmed', 'cancelled', 'completed', 'no_show')",
            name=op.f("ck_bookings_status"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_bookings_group_id_groups"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rescheduled_from_id"],
            ["bookings.id"],
            name=op.f("fk_bookings_rescheduled_from_id_bookings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"], ["slots.id"], name=op.f("fk_bookings_slot_id_slots"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
        sa.UniqueConstraint(
            "management_token_hash", name=op.f("uq_bookings_management_token_hash")
        ),
    )
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_bookings_group_id"), ["group_id"], unique=False)
        batch_op.create_index("ix_bookings_slot_status", ["slot_id", "status"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("run_at", sa.DateTime(), nullable=False),
        sa.Column("pinned_start", sa.DateTime(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')", name=op.f("ck_jobs_status")
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_jobs_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index("ix_jobs_booking_id", ["booking_id"], unique=False)
        batch_op.create_index("ix_jobs_status_run_at", ["status", "run_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_status_run_at")
        batch_op.drop_index("ix_jobs_booking_id")

    op.drop_table("jobs")
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_index("ix_bookings_slot_status")
        batch_op.drop_index(batch_op.f("ix_bookings_group_id"))

    op.drop_table("bookings")
    op.drop_table("slots")
    with op.batch_alter_table("schedule_rules", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_schedule_rules_group_id"))

    op.drop_table("schedule_rules")
    with op.batch_alter_table("schedule_overrides", schema=None) as batch_op:
        batch_op.drop_index("ix_schedule_overrides_group_day")

    op.drop_table("schedule_overrides")
    op.drop_table("reminder_rules")
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_groups_organisation_id"))

    op.drop_table("groups")
    op.drop_table("organisations")
