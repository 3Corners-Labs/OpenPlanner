"""Staffing blocks (ADR 0027 phase A): blocks with 0 agents and an optional label.

Relaxes the ``capacity >= 1`` checks on ``schedule_rules`` and ``schedule_overrides`` to
``>= 0`` and adds a nullable ``label`` (at most 50 characters, admin content only). The
downgrade deletes the blocks with 0 agents, which the old checks cannot hold; they offer no
slots, so availability is the same without them.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-26 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OVERRIDE_CHECK = (
    "(opens IS NULL AND closes IS NULL AND capacity IS NULL) "
    "OR (opens < closes AND capacity >= {minimum})"
)


def upgrade() -> None:
    with op.batch_alter_table("schedule_rules", schema=None) as batch_op:
        batch_op.add_column(sa.Column("label", sa.String(length=50), nullable=True))
        batch_op.drop_constraint("capacity", type_="check")
        batch_op.create_check_constraint("capacity", "capacity >= 0")
    with op.batch_alter_table("schedule_overrides", schema=None) as batch_op:
        batch_op.add_column(sa.Column("label", sa.String(length=50), nullable=True))
        batch_op.drop_constraint("window_or_closed", type_="check")
        batch_op.create_check_constraint("window_or_closed", _OVERRIDE_CHECK.format(minimum=0))


def downgrade() -> None:
    op.execute("DELETE FROM schedule_rules WHERE capacity = 0")
    op.execute("DELETE FROM schedule_overrides WHERE capacity = 0")
    with op.batch_alter_table("schedule_overrides", schema=None) as batch_op:
        batch_op.drop_constraint("window_or_closed", type_="check")
        batch_op.create_check_constraint("window_or_closed", _OVERRIDE_CHECK.format(minimum=1))
        batch_op.drop_column("label")
    with op.batch_alter_table("schedule_rules", schema=None) as batch_op:
        batch_op.drop_constraint("capacity", type_="check")
        batch_op.create_check_constraint("capacity", "capacity >= 1")
        batch_op.drop_column("label")
