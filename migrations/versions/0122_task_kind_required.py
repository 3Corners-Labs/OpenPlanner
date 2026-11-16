"""A booking's stored task always carries its kind (ADR 0123 point 8).

One task, of one kind, per contact moment. The booking's single task column already holds at
most one task; this makes the kind part of it in the database too:

* backfill: every booking that holds a task id without a kind (a task made before ADR 0123 step
  7a, when NULL meant a form) gets ``form``, which is what it is read as already;
* ``ck_bookings_task_kind``: a task id (``contact_center_task_id`` or the old ``wxcc_task_id``)
  is stored only with ``contact_center_task_kind``.

The check is one way on purpose. The purge deletes the task's id and keeps its kind (a word of a
closed list, ``app.domain.privacy``), so a kind without an id is a purged booking, not a fault.

Expand/contract (ADR 0116): tightened only where the previous minor already complies. Its image
(v0.6.0, which has migration 0116) writes the kind with every task it stores
(``task_columns``) and empties only ids in its purge, so it keeps working on this schema. The
table is rebuilt by Alembic's batch mode on SQLite, as for the earlier checks on ``bookings``.

Downgrade drops the check. The backfilled ``form`` stays: it means what NULL meant before.

Revision ID: 0122
Revises: 0121
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0122"
down_revision: str | None = "0121"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONDITION = (
    "(contact_center_task_id IS NULL AND wxcc_task_id IS NULL) "
    "OR contact_center_task_kind IS NOT NULL"
)
"""A stored task id carries its kind; the same text as the model's check."""


def upgrade() -> None:
    """Give every stored task without a kind the form, then require the kind."""
    op.execute(
        sa.text(
            "UPDATE bookings SET contact_center_task_kind = 'form' "
            "WHERE contact_center_task_kind IS NULL "
            "AND (contact_center_task_id IS NOT NULL OR wxcc_task_id IS NOT NULL)"
        )
    )
    with op.batch_alter_table("bookings") as batch:
        batch.create_check_constraint(op.f("ck_bookings_task_kind"), CONDITION)


def downgrade() -> None:
    """Drop the check; the backfilled kinds stay (``form`` is what NULL meant)."""
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint(op.f("ck_bookings_task_kind"), type_="check")
