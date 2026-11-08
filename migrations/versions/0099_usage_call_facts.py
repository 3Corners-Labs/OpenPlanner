"""What the call, rebooking and slot facts of reporting on use need (ADR 0117 step 3).

Adds, all nullable or defaulted (an expand step, ADR 0116): to ``call_status`` the largest
number of people seen in the meeting (``peak_participants``, metric U11) and when the first
guest was seen (``guest_joined_at``, metric Q4); to ``bookings`` when a planner asked for a new
time (``rebook_requested_at``, the time to accept of metric U8); to ``usage_rollup_state`` the
end of the last slot whose closing facts were written (``slots_closed_until``, metrics C1 to C5
and the outcome counts). None of them holds personal data.

Revision ID: 0099
Revises: 0098
Create Date: 2026-11-08 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0099"
down_revision: str | None = "0098"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the columns."""
    with op.batch_alter_table("call_status") as batch:
        batch.add_column(
            sa.Column(
                "peak_participants", sa.Integer(), nullable=False, server_default=sa.text("0")
            )
        )
        batch.add_column(sa.Column("guest_joined_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(
            sa.Column("rebook_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
    with op.batch_alter_table("usage_rollup_state") as batch:
        batch.add_column(sa.Column("slots_closed_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop the columns."""
    with op.batch_alter_table("usage_rollup_state") as batch:
        batch.drop_column("slots_closed_until")
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("rebook_requested_at")
    with op.batch_alter_table("call_status") as batch:
        batch.drop_column("guest_joined_at")
        batch.drop_column("peak_participants")
