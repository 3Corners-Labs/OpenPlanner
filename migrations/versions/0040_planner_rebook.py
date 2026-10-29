"""Ask to rebook (ADR 0044 step 3): status ``awaiting_rebook`` and ``bookings.rebook_by``.

A planner can ask the citizen to choose a new time. The booking frees its place, gets status
``awaiting_rebook`` and ``rebook_by``: when an unanswered request lapses (the booking is then
cancelled and its personal data purged 24 h later). ``rebook_by`` is a time, not personal
data.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-29 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "'confirmed', 'cancelled', 'completed', 'no_show', 'suspended'"


def upgrade() -> None:
    """Allow the new status and add ``rebook_by``."""
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("rebook_by", UTCDateTime(), nullable=True))
        batch.drop_constraint(op.f("ck_bookings_status"), type_="check")
        batch.create_check_constraint(
            op.f("ck_bookings_status"), f"status IN ({_STATUSES}, 'awaiting_rebook')"
        )


def downgrade() -> None:
    """Cancel what still awaits a new time, purge it at once, then drop the column.

    Without the status those bookings cannot exist; cancelling them with a purge job due now
    keeps no personal data longer than the request would have.
    """
    op.execute(
        "INSERT INTO jobs (booking_id, type, run_at, status, attempts) "
        "SELECT id, 'purge_pii', CURRENT_TIMESTAMP, 'pending', 0 FROM bookings "
        "WHERE status = 'awaiting_rebook'"
    )
    op.execute(
        "UPDATE bookings SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP "
        "WHERE status = 'awaiting_rebook'"
    )
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint(op.f("ck_bookings_status"), type_="check")
        batch.create_check_constraint(op.f("ck_bookings_status"), f"status IN ({_STATUSES})")
        batch.drop_column("rebook_by")
