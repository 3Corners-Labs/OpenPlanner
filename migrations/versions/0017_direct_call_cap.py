"""Cap on open direct calls per group (ADR 0026).

``direct_call_max_concurrent`` is NULL for no cap, which keeps the behaviour of existing groups.
The index serves the count of a group's recent direct calls.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.add_column(sa.Column("direct_call_max_concurrent", sa.Integer(), nullable=True))
    op.create_index("ix_slots_group_kind_start", "slots", ["group_id", "kind", "start"])


def downgrade() -> None:
    op.drop_index("ix_slots_group_kind_start", table_name="slots")
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.drop_column("direct_call_max_concurrent")
