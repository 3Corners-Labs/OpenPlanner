"""A refused booking (ADR 0066 step 8): status ``refused`` on ``bookings``.

A participant's booking over the address limit of its group no longer shows an error: the page
answers as for any booking, and a booking with status ``refused`` carries the one email that
explains why. It holds no place and its personal data is purged a day after the attempt.

Revision ID: 0106
Revises: 0105
Create Date: 2026-11-09 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0106"
down_revision: str | None = "0105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = (
    "'confirmed', 'cancelled', 'completed', 'no_show', 'suspended', 'awaiting_rebook', "
    "'pending_confirmation'"
)


def upgrade() -> None:
    """Allow the new status."""
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint(op.f("ck_bookings_status"), type_="check")
        batch.create_check_constraint(
            op.f("ck_bookings_status"), f"status IN ({_STATUSES}, 'refused')"
        )


def downgrade() -> None:
    """Turn refused bookings into cancelled ones with a purge due now, then drop the status.

    Without the status those rows cannot exist; they never held a place, and purging them at
    once keeps no personal data longer than the refusal would have.
    """
    op.execute(
        "INSERT INTO jobs (booking_id, type, run_at, status, attempts) "
        "SELECT id, 'purge_pii', CURRENT_TIMESTAMP, 'pending', 0 FROM bookings "
        "WHERE status = 'refused'"
    )
    op.execute(
        "UPDATE bookings SET status = 'cancelled', cancelled_at = created_at "
        "WHERE status = 'refused'"
    )
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint(op.f("ck_bookings_status"), type_="check")
        batch.create_check_constraint(op.f("ck_bookings_status"), f"status IN ({_STATUSES})")
