"""Usage facts and their hourly rollup, without personal data (ADR 0117 step 2).

Three tables, all expand-only (nothing existing is touched, so the previous minor runs on the
same database):

- ``usage_events``: one row per fact as written, the outbox the worker rolls up and the source
  for a rebuild. Hour-truncated time, codes and numbers from the allow-list
  (``privacy.USAGE_FIELDS``); no booking id, so a row cannot be joined back to an appointment.
- ``usage_hour``: the long-lived rollup, one row per hour, organisation, group, metric and cut.
- ``usage_staff_hour``: the same per team member, written only while an organisation has the
  team view on (step 7); ``staff_id`` NULL is the folded *former team members* row.

There are no foreign keys: statistics outlive the groups and organisations they describe, as
audit events do. The two tables of the digest (subscriptions and run records) come with step 10
in a migration of their own, since nothing reads them before then. The downgrade drops the
tables; the statistics cannot be rebuilt from what the purge leaves.

Revises 0089 (audit events action index). Open PRs also claim numbers, so recheck the head
before merging.

Revision ID: 0090
Revises: 0089
Create Date: 2026-11-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``usage_events``, ``usage_hour`` and ``usage_staff_hour``."""
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
    )
    op.create_index(
        "ix_usage_events_scope_hour",
        "usage_events",
        ["organisation_id", "group_id", "bucket_hour"],
    )
    op.create_index("ix_usage_events_bucket_hour", "usage_events", ["bucket_hour"])
    op.create_table(
        "usage_hour",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bucket_hour", sa.DateTime(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=8), nullable=False),
        sa.Column("booking_kind", sa.String(length=16), nullable=True),
        sa.Column("dispatch_kind", sa.String(length=24), nullable=True),
        sa.Column("subject_code", sa.String(length=40), nullable=True),
        sa.Column("locale", sa.String(length=12), nullable=True),
        sa.Column("channel_kind", sa.String(length=16), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("actor_class", sa.String(length=16), nullable=True),
        sa.Column("reason_code", sa.String(length=40), nullable=True),
        sa.Column("bucket", sa.Integer(), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("sum_value", sa.Integer(), nullable=False),
        sa.Column("max_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_hour")),
    )
    op.create_index(
        "ix_usage_hour_scope_metric_hour",
        "usage_hour",
        ["organisation_id", "group_id", "metric", "bucket_hour"],
    )
    op.create_table(
        "usage_staff_hour",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bucket_hour", sa.DateTime(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("staff_id", sa.Integer(), nullable=True),
        sa.Column("metric", sa.String(length=8), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("sum_value", sa.Integer(), nullable=False),
        sa.Column("max_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_staff_hour")),
    )
    op.create_index(
        "ix_usage_staff_hour_scope_metric_hour",
        "usage_staff_hour",
        ["organisation_id", "group_id", "metric", "bucket_hour"],
    )


def downgrade() -> None:
    """Drop the three tables."""
    op.drop_table("usage_staff_hour")
    op.drop_table("usage_hour")
    op.drop_table("usage_events")
