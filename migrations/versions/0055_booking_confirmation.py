"""Confirm a booking by email link (ADR 0056 step 2): status, ``confirm_by``, ``confirmed_at``.

A citizen's new booking starts as ``pending_confirmation``: it holds its place until it is
confirmed or lapses at ``confirm_by``. ``confirmed_at`` records when the citizen confirmed,
which is also when the email address was verified. Both are times, not personal data.

Revision ID: 0055
Revises: 0053
Create Date: 2026-10-30 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0055"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "'confirmed', 'cancelled', 'completed', 'no_show', 'suspended', 'awaiting_rebook'"


def upgrade() -> None:
    """Allow the new status and add the two moments."""
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("confirm_by", UTCDateTime(), nullable=True))
        batch.add_column(sa.Column("confirmed_at", UTCDateTime(), nullable=True))
        batch.alter_column("status", type_=sa.String(24), existing_type=sa.String(16))
        batch.drop_constraint(op.f("ck_bookings_status"), type_="check")
        batch.create_check_constraint(
            op.f("ck_bookings_status"), f"status IN ({_STATUSES}, 'pending_confirmation')"
        )


def downgrade() -> None:
    """Cancel what is still unconfirmed, purge it at once, then drop the columns.

    Without the status those bookings cannot exist, and their address was never verified;
    cancelling them with a purge job due now keeps no personal data longer than the lapse
    would have.
    """
    op.execute(
        "INSERT INTO jobs (booking_id, type, run_at, status, attempts) "
        "SELECT id, 'purge_pii', CURRENT_TIMESTAMP, 'pending', 0 FROM bookings "
        "WHERE status = 'pending_confirmation'"
    )
    op.execute(
        "UPDATE bookings SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP "
        "WHERE status = 'pending_confirmation'"
    )
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint(op.f("ck_bookings_status"), type_="check")
        batch.create_check_constraint(op.f("ck_bookings_status"), f"status IN ({_STATUSES})")
        batch.alter_column("status", type_=sa.String(16), existing_type=sa.String(24))
        batch.drop_column("confirmed_at")
        batch.drop_column("confirm_by")
