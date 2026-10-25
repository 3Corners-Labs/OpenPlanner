"""Webex webhook deduplication: recently seen event keys (no personal data).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-25 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``webhook_events``: source and event digest, with the time it was seen."""
    op.create_table(
        "webhook_events",
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column("seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("source", "event_key", name=op.f("pk_webhook_events")),
    )
    op.create_index("ix_webhook_events_seen_at", "webhook_events", ["seen_at"])


def downgrade() -> None:
    """Drop ``webhook_events``; only replay protection for recent events is lost."""
    op.drop_index("ix_webhook_events_seen_at", table_name="webhook_events")
    op.drop_table("webhook_events")
