"""The rollup's watermark and the backfill's record (ADR 0117 step 4).

``usage_rollup_state`` is one row: the id of the last fact the worker rolled into ``usage_hour``,
when it did, when the backfill of old bookings was completed, and the hour from which live
facts exist (the backfill never repeats what live code wrote). The rollup advances the
watermark in the same transaction as the counts, so a crash neither double-counts nor loses
a fact. ``usage_backfill`` holds one row per booking already
turned into facts by ``python -m app.cli usage-backfill`` (idempotence); it holds a booking id
and nothing else, and a booking row goes with its row. Both are expand-only and hold no
personal data.

Revises 0096 (dispatch fallback). Open PRs also claim numbers, so recheck the head before
merging.

Revision ID: 0097
Revises: 0096
Create Date: 2026-11-08 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0097"
down_revision: str | None = "0096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rebuild_usage_events_with_autoincrement() -> None:
    """Rebuild ``usage_events`` so SQLite never reuses an id.

    Without ``AUTOINCREMENT`` a new row takes the largest id plus one, so after retention has
    deleted the newest rows a new fact would reuse an id below the rollup's watermark and never
    be rolled up. The table is new and holds only facts, which are copied across.
    """
    op.execute(
        "CREATE TABLE usage_events_copy AS SELECT id, kind, bucket_hour, organisation_id, "
        "group_id, booking_kind, dispatch_kind, subject_code, locale, channel_kind, outcome, "
        "actor_class, reason_code, value_seconds, participants, capacity, booked, staff_id "
        "FROM usage_events"
    )
    op.drop_table("usage_events")
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("bucket_hour", sa.DateTime(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("booking_kind", sa.String(length=16), nullable=True),
        sa.Column("dispatch_kind", sa.String(length=24), nullable=True),
        sa.Column("subject_code", sa.String(length=40), nullable=True),
        sa.Column("locale", sa.String(length=12), nullable=True),
        sa.Column("channel_kind", sa.String(length=16), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("actor_class", sa.String(length=16), nullable=True),
        sa.Column("reason_code", sa.String(length=40), nullable=True),
        sa.Column("value_seconds", sa.Integer(), nullable=True),
        sa.Column("participants", sa.Integer(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("booked", sa.Integer(), nullable=True),
        sa.Column("staff_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_usage_events_scope_hour",
        "usage_events",
        ["organisation_id", "group_id", "bucket_hour"],
    )
    op.create_index("ix_usage_events_bucket_hour", "usage_events", ["bucket_hour"])
    op.execute(
        "INSERT INTO usage_events (id, kind, bucket_hour, organisation_id, group_id, "
        "booking_kind, dispatch_kind, subject_code, locale, channel_kind, outcome, actor_class, "
        "reason_code, value_seconds, participants, capacity, booked, staff_id) "
        "SELECT id, kind, bucket_hour, organisation_id, group_id, booking_kind, dispatch_kind, "
        "subject_code, locale, channel_kind, outcome, actor_class, reason_code, value_seconds, "
        "participants, capacity, booked, staff_id FROM usage_events_copy"
    )
    op.drop_table("usage_events_copy")


def upgrade() -> None:
    """Make ``usage_events`` ids final; create ``usage_rollup_state`` and ``usage_backfill``."""
    _rebuild_usage_events_with_autoincrement()
    op.create_table(
        "usage_rollup_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.Column("rolled_at", sa.DateTime(), nullable=True),
        sa.Column("backfilled_at", sa.DateTime(), nullable=True),
        sa.Column("backfill_cutoff", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_rollup_state")),
    )
    op.create_table(
        "usage_backfill",
        sa.Column("booking_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_usage_backfill_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("booking_id", name=op.f("pk_usage_backfill")),
    )


def downgrade() -> None:
    """Drop both tables; ``usage_events`` keeps its autoincrement, which does no harm."""
    op.drop_table("usage_backfill")
    op.drop_table("usage_rollup_state")
