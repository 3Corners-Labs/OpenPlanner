"""The provider's reason on failed jobs (ADR 0065).

``audit_events.message`` and ``booking_events.message`` hold Webex's own words on a refusal
(addresses masked, at most 300 characters, only from requests without citizen data), so the
booking's timeline and the audit log's chain show why a meeting could not be scheduled.

Revises 0054 (bookings per email address); the open branch 0055 claims its own number.

The downgrade drops both columns; the reason then lives only in the container log.

Revision ID: 0056
Revises: 0054
Create Date: 2026-10-30 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("audit_events", "booking_events")


def upgrade() -> None:
    """Add ``message`` to the audit and timeline events."""
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "message", sa.String(length=300), server_default=sa.text("''"), nullable=False
                )
            )


def downgrade() -> None:
    """Drop ``message`` again."""
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("message")
