"""The fallback of a contact-center group to the own queue (ADR 0082 step 4).

Adds to ``groups`` the choice ``dispatch_fallback`` (``none`` by default), the team's own Webex
space for fallback messages and ``fallback_active_since`` (the group's state while the contact
center is down), and to ``call_status`` the flag ``fallback`` of a booking in the own queue as a
fallback. Defaulted and nullable columns only (an expand step, ADR 0116); the downgrade drops
them.

Revision ID: 0094
Revises: 0093
Create Date: 2026-11-08 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0094"
down_revision: str | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the fallback columns."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "dispatch_fallback",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'none'"),
            )
        )
        batch.add_column(sa.Column("fallback_space_room_id", sa.String(200), nullable=True))
        batch.add_column(
            sa.Column("fallback_active_since", sa.DateTime(timezone=True), nullable=True)
        )
    with op.batch_alter_table("call_status") as batch:
        batch.add_column(
            sa.Column("fallback", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    """Drop the fallback columns."""
    with op.batch_alter_table("call_status") as batch:
        batch.drop_column("fallback")
    with op.batch_alter_table("groups") as batch:
        batch.drop_column("fallback_active_since")
        batch.drop_column("fallback_space_room_id")
        batch.drop_column("dispatch_fallback")
