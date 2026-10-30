"""Direct calls per staffing block instead of per group (ADR 0072).

Every staffing block (``schedule_rules``) and every exception with a window
(``schedule_overrides``) gets an ``offer``: ``scheduled`` (slots, the default), ``direct``
(direct calls only) or ``both``. The group switch ``direct_call_enabled`` goes: the blocks of
a group that had it on become ``both``, so citizens see no change.

Revises 0063 (trial call, ADR 0071), the head of ``main`` at merge time.

The downgrade sets the switch again for every group with a block that offers direct calls,
then drops the ``offer`` columns.

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-30 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BLOCK_TABLES = ("schedule_rules", "schedule_overrides")
OFFERS = "('scheduled', 'direct', 'both')"


def upgrade() -> None:
    """Add ``offer`` to blocks and exceptions, convert the group switch, then drop it."""
    for table in BLOCK_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "offer",
                    sa.String(length=9),
                    server_default=sa.text("'scheduled'"),
                    nullable=False,
                )
            )
            batch.create_check_constraint("offer", f"offer IN {OFFERS}")
        op.execute(
            f"UPDATE {table} SET offer = 'both' "  # noqa: S608 - table name from a constant
            "WHERE group_id IN (SELECT id FROM groups WHERE direct_call_enabled = 1)"
        )
    with op.batch_alter_table("groups") as batch:
        batch.drop_column("direct_call_enabled")


def downgrade() -> None:
    """Bring the group switch back from the blocks and drop ``offer``."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "direct_call_enabled",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
    op.execute(
        "UPDATE groups SET direct_call_enabled = 1 WHERE id IN ("
        "SELECT group_id FROM schedule_rules WHERE offer IN ('direct', 'both') "
        "UNION SELECT group_id FROM schedule_overrides WHERE offer IN ('direct', 'both'))"
    )
    for table in BLOCK_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint("offer", type_="check")
            batch.drop_column("offer")
