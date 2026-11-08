"""Alert targets and what was delivered to them (ADR 0082 step 3).

Creates ``alert_targets`` (an email address or a Webex room id per scope, with a minimum level)
and ``alert_deliveries`` (per target and alert: the streak sent, when, and the retry state).
Neither holds participant data. The downgrade drops both.

Revision ID: 0095
Revises: 0093
Create Date: 2026-11-08 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0095"
down_revision: str | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the two tables."""
    op.create_table(
        "alert_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organisation_id",
            sa.Integer(),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("min_level", sa.String(16), nullable=False),
        sa.CheckConstraint("kind IN ('email', 'webex_space')", name=op.f("ck_alert_targets_kind")),
        sa.CheckConstraint(
            "min_level IN ('warning', 'outage')", name=op.f("ck_alert_targets_min_level")
        ),
    )
    op.create_index("ix_alert_targets_organisation_id", "alert_targets", ["organisation_id"])
    op.create_table(
        "alert_deliveries",
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("alert_targets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("alert_key", sa.String(255), primary_key=True),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the two tables."""
    op.drop_table("alert_deliveries")
    op.drop_index("ix_alert_targets_organisation_id", table_name="alert_targets")
    op.drop_table("alert_targets")
