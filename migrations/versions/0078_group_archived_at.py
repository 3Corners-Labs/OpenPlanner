"""Add ``groups.archived_at`` (ADR 0084 step 2).

An archived group keeps its row and slug until the nightly sweep erases it (step 3); the
column says when it was archived and NULL means in use. The downgrade drops the column, which
puts every archived group back in use.

Revises 0077 (participant change lead). Open PRs also claim numbers, so recheck the head
before merging.

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-31 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable column."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(sa.Column("archived_at", UTCDateTime(), nullable=True))


def downgrade() -> None:
    """Drop the column; archived groups are in use again."""
    with op.batch_alter_table("groups") as batch:
        batch.drop_column("archived_at")
