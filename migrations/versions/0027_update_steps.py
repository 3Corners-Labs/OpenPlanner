"""Update steps (ADR 0048): the step the host updater reported last, and when.

``update_requests`` gains ``step`` and ``step_at`` so the updates page can show where a running
update or restore is. No personal data. The downgrade drops both columns; a restore to a
version without this feature then simply no longer shows steps.

Revision ID: 0027
Revises: 0025
Create Date: 2026-10-28 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STEPS = (
    "step IS NULL OR step IN ('pulling', 'verifying', 'backing_up', 'migrating', "
    "'starting', 'rolling_back')"
)


def upgrade() -> None:
    """Add ``step`` and ``step_at`` to ``update_requests``."""
    with op.batch_alter_table("update_requests") as batch:
        batch.add_column(sa.Column("step", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("step_at", sa.DateTime(), nullable=True))
        batch.create_check_constraint(op.f("ck_update_requests_step"), _STEPS)


def downgrade() -> None:
    """Drop ``step`` and ``step_at``."""
    with op.batch_alter_table("update_requests") as batch:
        batch.drop_constraint(op.f("ck_update_requests_step"), type_="check")
        batch.drop_column("step_at")
        batch.drop_column("step")
