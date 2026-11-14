"""When a guest was first minted for a booking at the video platform (ADR 0123 step 4a).

Expand only (ADR 0116): one nullable column on ``bookings``, no default, so no row of the large
table is written. The previous image never reads or writes it, so it keeps working on this
schema and a restore into it loses nothing it knew.

* ``bookings.guest_minted_at``: set once, by the call page's token endpoint, when a guest is
  first minted for the booking. NULL for every booking before this step and for every booking
  that never used the call page. A moment, no personal data; the purge does not empty it: it
  reads it to enqueue the rename of the guest at the platform (``rename_guest``).

Downgrade drops the column. Nothing else depends on it; a booking minted under this schema and
purged after the downgrade keeps the guest's name at the platform (what the previous image did).

Revision ID: 0117
Revises: 0116
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0117"
down_revision: str | None = "0116"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable moment a guest was first minted."""
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("guest_minted_at", UTCDateTime(), nullable=True))


def downgrade() -> None:
    """Drop the column."""
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("guest_minted_at")
