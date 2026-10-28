"""Own queue: the team's address for calendar invites (ADR 0043 point 9).

``groups`` gains ``calendar_invite_address`` (nullable; no invite when empty). An address of
the organisation's team, not of a citizen.

Also the merge point of ``0043`` (own-queue claims) and ``0051`` (organisation settings), which
were merged in parallel on ``0050``; merged migrations are never edited, so the two heads are
joined here.

Revision ID: 0044
Revises: 0043, 0051
Create Date: 2026-10-29 01:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | tuple[str, ...] | None = ("0043", "0051")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the address column."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(sa.Column("calendar_invite_address", sa.String(length=254), nullable=True))


def downgrade() -> None:
    """Drop the column; no more invites are sent."""
    with op.batch_alter_table("groups") as batch:
        batch.drop_column("calendar_invite_address")
