"""A planner's proposal of a new time (ADR 0057 step 2): booking status ``proposed``.

A proposal is a booking in the proposed slot that holds its place until the citizen confirms
it or ``confirm_by`` passes; ``rescheduled_from_id`` points at the booking it would replace
and ``change_reason`` carries the planner's reason. Only the status is new.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-30 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
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
            op.f("ck_bookings_status"), f"status IN ({_STATUSES}, 'proposed')"
        )


def downgrade() -> None:
    """Release open proposals, purge them at once, then drop the status.

    The bookings they would replace keep awaiting a new time, as after a release.
    """
    op.execute(
        "INSERT INTO jobs (booking_id, type, run_at, status, attempts) "
        "SELECT id, 'purge_pii', CURRENT_TIMESTAMP, 'pending', 0 FROM bookings "
        "WHERE status = 'proposed'"
    )
    op.execute(
        "UPDATE bookings SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP "
        "WHERE status = 'proposed'"
    )
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint(op.f("ck_bookings_status"), type_="check")
        batch.create_check_constraint(op.f("ck_bookings_status"), f"status IN ({_STATUSES})")
