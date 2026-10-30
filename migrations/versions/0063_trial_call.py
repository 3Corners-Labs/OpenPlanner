"""Trial call on Webex ahead of the appointment (ADR 0071).

``groups`` gains ``trial_call_enabled`` (default on), ``trial_call_minutes`` (30, 10 to 60) and
``trial_calls_per_booking`` (3, 1 to 5). ``bookings`` gains the trial meeting's id, join link
(personal data, purged with the contact), the moment it closes, and a count of trial calls.

Revises 0062 (citizen change lead). Recheck the head before merging: parallel branches claim
numbers.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-30 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the group settings and the booking's trial columns with their checks."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "trial_call_enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False
            )
        )
        batch.add_column(
            sa.Column(
                "trial_call_minutes", sa.Integer(), server_default=sa.text("30"), nullable=False
            )
        )
        batch.add_column(
            sa.Column(
                "trial_calls_per_booking", sa.Integer(), server_default=sa.text("3"), nullable=False
            )
        )
        batch.create_check_constraint("trial_call_minutes", "trial_call_minutes BETWEEN 10 AND 60")
        batch.create_check_constraint(
            "trial_calls_per_booking", "trial_calls_per_booking BETWEEN 1 AND 5"
        )
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("trial_room_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("trial_join_url", sa.String(length=2048), nullable=True))
        batch.add_column(sa.Column("trial_expires_at", UTCDateTime(), nullable=True))
        batch.add_column(
            sa.Column("trial_count", sa.Integer(), server_default=sa.text("0"), nullable=False)
        )


def downgrade() -> None:
    """Drop the booking columns and the group settings again."""
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("trial_count")
        batch.drop_column("trial_expires_at")
        batch.drop_column("trial_join_url")
        batch.drop_column("trial_room_id")
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("trial_calls_per_booking", type_="check")
        batch.drop_constraint("trial_call_minutes", type_="check")
        batch.drop_column("trial_calls_per_booking")
        batch.drop_column("trial_call_minutes")
        batch.drop_column("trial_call_enabled")
