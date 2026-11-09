"""The digest of the usage reports: subscriptions and run records (ADR 0117 step 10).

Two new tables, expand only (ADR 0116): the previous image neither reads nor writes them.

- ``report_subscriptions``: who gets a digest of what, how often. The recipients are the row ids
  of staff members in a comma-separated list, never an address; the address is read when the
  mail goes out.
- ``report_digest_runs``: one row per due digest, unique on (subscription, due date), so a retry
  or a second worker never sends the same digest twice.

A subscription belongs to an organisation or one group and goes with it. Open PRs also claim
numbers, so recheck the head before merging.

Revision ID: 0103
Revises: 0102
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0103"
down_revision: str | None = "0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``report_subscriptions`` and ``report_digest_runs``."""
    op.create_table(
        "report_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("weekday", sa.Integer(), server_default="0", nullable=False),
        sa.Column("month_day", sa.Integer(), server_default="1", nullable=False),
        sa.Column("recipients", sa.String(length=1000), server_default="", nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "period IN ('daily', 'weekly', 'monthly')",
            name=op.f("ck_report_subscriptions_period"),
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=op.f("ck_report_subscriptions_weekday")),
        sa.CheckConstraint(
            "month_day BETWEEN 1 AND 28", name=op.f("ck_report_subscriptions_month_day")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_report_subscriptions_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_report_subscriptions_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_subscriptions")),
    )
    op.create_index(
        "ix_report_subscriptions_organisation_id", "report_subscriptions", ["organisation_id"]
    )
    op.create_table(
        "report_digest_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('sent', 'skipped_late', 'no_recipient')",
            name=op.f("ck_report_digest_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["report_subscriptions.id"],
            name=op.f("fk_report_digest_runs_subscription_id_report_subscriptions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_digest_runs")),
        sa.UniqueConstraint(
            "subscription_id",
            "due_date",
            name=op.f("uq_report_digest_runs_subscription_id_due_date"),
        ),
    )


def downgrade() -> None:
    """Drop the two tables."""
    op.drop_table("report_digest_runs")
    op.drop_index("ix_report_subscriptions_organisation_id", table_name="report_subscriptions")
    op.drop_table("report_subscriptions")
