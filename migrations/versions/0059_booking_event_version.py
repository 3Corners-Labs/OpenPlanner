"""The version that wrote a timeline event (ADR 0068).

``booking_events.version`` holds the ``APP_VERSION`` of the process that wrote the event, so
an admin can tell an event of an older version from the check that ran on a newer one. Events
written before this migration keep an empty version, which the page shows as unknown.

Revises 0058 (booking confirmation, ADR 0056 step 2).

The downgrade drops the column; the request events (ADR 0068) are ordinary audit events and
need no schema change.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-30 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``version`` to the timeline events."""
    with op.batch_alter_table("booking_events") as batch:
        batch.add_column(
            sa.Column("version", sa.String(length=40), server_default=sa.text("''"), nullable=False)
        )


def downgrade() -> None:
    """Drop ``version`` again."""
    with op.batch_alter_table("booking_events") as batch:
        batch.drop_column("version")
