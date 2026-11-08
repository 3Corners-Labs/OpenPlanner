"""Lobby join time and the not-picked-up report on the call status (ADR 0043 step 4).

Two nullable columns on ``call_status``, nothing to backfill (expand only, ADR 0116):

- ``lobby_since``: when the participant joined and started waiting for an agent, so "pick up
  next" takes the one who waited longest;
- ``not_picked_up_at``: when the worker reported the booking as not picked up, so the system
  event is recorded once.

The previous image does not read either column.

Revision ID: 0100
Revises: 0098
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0100"
down_revision: str | None = "0098"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two columns."""
    with op.batch_alter_table("call_status") as batch:
        batch.add_column(sa.Column("lobby_since", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("not_picked_up_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop the two columns."""
    with op.batch_alter_table("call_status") as batch:
        batch.drop_column("not_picked_up_at")
        batch.drop_column("lobby_since")
