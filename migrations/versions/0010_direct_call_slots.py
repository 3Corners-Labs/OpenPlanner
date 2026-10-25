"""Direct call slots (ADR 0019): slot kind, uniqueness only for scheduled slots.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-24 01:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("slots", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "kind", sa.String(length=16), server_default=sa.text("'scheduled'"), nullable=False
            )
        )
        batch_op.drop_constraint("uq_slots_group_id_start", type_="unique")
        batch_op.create_check_constraint("kind", "kind IN ('scheduled', 'direct')")
    op.create_index(
        "uq_slots_group_start_scheduled",
        "slots",
        ["group_id", "start"],
        unique=True,
        sqlite_where=sa.text("kind = 'scheduled'"),
    )


def downgrade() -> None:
    # Direct calls cannot be represented without the kind column; refuse rather than delete.
    direct = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM slots WHERE kind = 'direct'"))
    if direct.scalar_one():
        raise RuntimeError("direct-call slots exist; downgrading would lose bookings")
    op.drop_index("uq_slots_group_start_scheduled", table_name="slots")
    with op.batch_alter_table("slots", schema=None) as batch_op:
        batch_op.drop_constraint("kind", type_="check")
        batch_op.create_unique_constraint("uq_slots_group_id_start", ["group_id", "start"])
        batch_op.drop_column("kind")
