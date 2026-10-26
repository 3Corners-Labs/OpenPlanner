"""System events in the audit log (ADR 0029): source, booking id, retry and skipped outcomes.

Adds ``source`` (``admin`` for every existing row) and a nullable ``booking_id`` (no foreign
key: events outlive bookings), widens the outcome check with ``retry`` and ``skipped``, and
indexes the group column (the group admin's view) and source plus time (pruning). The
downgrade deletes the system events, which the old table cannot describe.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-26 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "source", sa.String(length=16), server_default=sa.text("'admin'"), nullable=False
            )
        )
        batch_op.add_column(sa.Column("booking_id", sa.Integer(), nullable=True))
        batch_op.drop_constraint("outcome", type_="check")
        batch_op.create_check_constraint(
            "outcome", "outcome IN ('success', 'denied', 'failure', 'retry', 'skipped')"
        )
        batch_op.create_check_constraint("source", "source IN ('admin', 'system')")
        batch_op.create_index("ix_audit_events_group", ["group"], unique=False)
        batch_op.create_index("ix_audit_events_source_at", ["source", "at"], unique=False)


def downgrade() -> None:
    op.execute("DELETE FROM audit_events WHERE source = 'system'")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_events_source_at")
        batch_op.drop_index("ix_audit_events_group")
        batch_op.drop_constraint("source", type_="check")
        batch_op.drop_constraint("outcome", type_="check")
        batch_op.create_check_constraint("outcome", "outcome IN ('success', 'denied', 'failure')")
        batch_op.drop_column("booking_id")
        batch_op.drop_column("source")
