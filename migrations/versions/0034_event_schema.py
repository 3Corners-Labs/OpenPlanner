"""One event schema for the audit log (ADR 0040, part 2): event id, level, category, trace.

``audit_events`` gains ``event_id`` (unique, so a copy on a log platform can be matched to its
row), ``level`` and ``category`` (with checks on their values), ``trace_id`` (indexed, to
follow one request or job) and ``service`` (``web`` or ``worker``). Existing rows get a random
event id, the level that follows from their outcome and the category that follows from their
source; they have no trace or service. Ids and codes only; no personal data of citizens.

Revision ID: 0034
Revises: 0026
Create Date: 2026-10-28 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the columns, fill them for existing rows, then index and constrain them.

    The columns are added in place (no table copy); one batch then makes ``event_id``
    required and adds the checks.
    """
    op.add_column("audit_events", sa.Column("event_id", sa.String(length=32), nullable=True))
    op.add_column(
        "audit_events",
        sa.Column("level", sa.String(length=16), server_default=sa.text("'info'"), nullable=False),
    )
    op.add_column(
        "audit_events",
        sa.Column(
            "category", sa.String(length=16), server_default=sa.text("'audit'"), nullable=False
        ),
    )
    op.add_column(
        "audit_events",
        sa.Column("trace_id", sa.String(length=32), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "audit_events",
        sa.Column("service", sa.String(length=16), server_default=sa.text("''"), nullable=False),
    )
    op.execute(
        "UPDATE audit_events SET event_id = lower(hex(randomblob(16))), "
        "level = CASE outcome WHEN 'failure' THEN 'error' "
        "WHEN 'denied' THEN 'warning' WHEN 'retry' THEN 'warning' ELSE 'info' END, "
        "category = CASE source WHEN 'system' THEN 'system' ELSE 'audit' END"
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.alter_column("event_id", existing_type=sa.String(length=32), nullable=False)
        batch_op.create_check_constraint(
            "level", "level IN ('debug', 'info', 'warning', 'error', 'critical')"
        )
        batch_op.create_check_constraint(
            "category", "category IN ('audit', 'system', 'security', 'diagnostic')"
        )
    op.create_index("ix_audit_events_event_id", "audit_events", ["event_id"], unique=True)
    op.create_index("ix_audit_events_trace_id", "audit_events", ["trace_id"], unique=False)


def downgrade() -> None:
    """Drop the columns; events of the new categories cannot be described without them."""
    op.execute("DELETE FROM audit_events WHERE category IN ('security', 'diagnostic')")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_constraint("category", type_="check")
        batch_op.drop_constraint("level", type_="check")
        batch_op.drop_index("ix_audit_events_trace_id")
        batch_op.drop_index("ix_audit_events_event_id")
        batch_op.drop_column("service")
        batch_op.drop_column("trace_id")
        batch_op.drop_column("category")
        batch_op.drop_column("level")
        batch_op.drop_column("event_id")
