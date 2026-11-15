"""The messages of the chat beside the participant's call (ADR 0123 point 8, step 7b).

Expand only (ADR 0116): one new table, nothing existing is touched or backfilled. The previous
image never reads or writes it, so it keeps working on this schema, and a restore into it loses
nothing it knew.

* ``call_messages``: one row per message of a booking's conversation. ``author`` is
  ``participant`` or ``staff`` (a kind, never a name), ``message_text`` the text (personal data,
  on the one list of ``app.domain.privacy`` as ``CHAT_FIELDS``, deleted with the booking's purge),
  ``at`` the time, ``state`` ``sent``, ``delivered`` or ``refused`` and ``provider_message_id`` the
  platform's identifier of the message: unique per booking, so a delivery that comes twice is one
  row. The rows go with their booking (``ON DELETE CASCADE``) and with its purge.

Downgrade drops the table; the messages of a conversation are lost, which is what a rollback past
this step means (the contact center keeps its own transcript).

Revision ID: 0121
Revises: 0120
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0121"
down_revision: str | None = "0120"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table of chat messages and its index."""
    op.create_table(
        "call_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "booking_id",
            sa.Integer(),
            sa.ForeignKey(
                "bookings.id", ondelete="CASCADE", name=op.f("fk_call_messages_booking_id_bookings")
            ),
            nullable=False,
        ),
        sa.Column("author", sa.String(length=16), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("at", UTCDateTime(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("provider_message_id", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "author IN ('participant', 'staff')", name=op.f("ck_call_messages_author")
        ),
        sa.CheckConstraint(
            "state IN ('sent', 'delivered', 'refused')", name=op.f("ck_call_messages_state")
        ),
        sa.UniqueConstraint(
            "booking_id",
            "provider_message_id",
            name=op.f("uq_call_messages_booking_id_provider_message_id"),
        ),
    )
    op.create_index("ix_call_messages_booking_id_at", "call_messages", ["booking_id", "at"])


def downgrade() -> None:
    """Drop the table."""
    op.drop_index("ix_call_messages_booking_id_at", table_name="call_messages")
    op.drop_table("call_messages")
