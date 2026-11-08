"""Subjects and routing attributes next to the group (ADR 0081).

Adds nullable columns only (expand step, ADR 0116): ``groups.routing`` (the subjects, switched-on
attributes and routing mapping as JSON), ``bookings.subject_code`` and
``bookings.routing_answers`` (the participant's codes), and ``subjects`` on the two staffing
block tables (the subject codes a block offers). None of them holds personal data. The
downgrade drops the columns again.

Revision ID: 0090
Revises: 0089
Create Date: 2026-11-08 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BLOCK_TABLES = ("schedule_rules", "schedule_overrides")


def upgrade() -> None:
    """Add the routing columns."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(sa.Column("routing", sa.Text(), nullable=True))
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("subject_code", sa.String(40), nullable=True))
        batch.add_column(sa.Column("routing_answers", sa.String(500), nullable=True))
    for table in _BLOCK_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("subjects", sa.String(1000), nullable=True))


def downgrade() -> None:
    """Drop the routing columns."""
    for table in _BLOCK_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("subjects")
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("routing_answers")
        batch.drop_column("subject_code")
    with op.batch_alter_table("groups") as batch:
        batch.drop_column("routing")
