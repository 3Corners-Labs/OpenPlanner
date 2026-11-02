"""Staff status: suspended and erased people (ADR 0046 step 2).

``staff.status`` is ``active``, ``suspended`` (no portal access whatever the claims say) or
``erased`` (name, roles, memberships and refused values removed; the subject stays so the
audit log still resolves to "erased person"). ``status_at`` is when the status last changed.
Existing rows are active.

Revises 0083 (participant link sessions, ADR 0093). Open PRs also claim numbers, so recheck
the head before merging.

Revision ID: 0085
Revises: 0083
Create Date: 2026-11-02 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0085"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``status`` and ``status_at`` to ``staff``."""
    with op.batch_alter_table("staff") as batch:
        batch.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active")
        )
        batch.add_column(sa.Column("status_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index(op.f("ix_staff_status"), ["status"])


def downgrade() -> None:
    """Drop ``status`` and ``status_at``; an erased record keeps its empty name."""
    with op.batch_alter_table("staff") as batch:
        batch.drop_index(op.f("ix_staff_status"))
        batch.drop_column("status_at")
        batch.drop_column("status")
