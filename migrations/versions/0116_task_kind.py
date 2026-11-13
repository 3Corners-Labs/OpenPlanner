"""The kind of contact-center task, on the routing and on the task (ADR 0123 step 7a).

Expand only (ADR 0116): two columns, nothing dropped, renamed or tightened. The previous image
never reads or writes either, so it keeps working on this schema and a restore into it loses
nothing it knew.

* ``group_contact_center.task_kind``: what a request becomes at the platform, ``form`` (the
  work item, the default) or ``conversation`` (a messaging task). Every existing row is a form.
* ``bookings.contact_center_task_kind``: the kind a task was created with, so a later step
  knows a conversation without asking the platform. NULL is a form, which every task before
  this step is; the column is nullable so that no row of the large table is written.

Downgrade drops both. It refuses while a conversation exists, whether as a routing or as a
task: without the columns a conversation task would be read as a work item.

Revision ID: 0116
Revises: 0115
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0116"
down_revision: str | None = "0115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the routing's task kind (default ``form``) and the task's own (nullable)."""
    with op.batch_alter_table("group_contact_center") as batch:
        batch.add_column(
            sa.Column(
                "task_kind", sa.String(length=16), server_default=sa.text("'form'"), nullable=False
            )
        )
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("contact_center_task_kind", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Drop both columns; refuse while a conversation routing or task exists."""
    bind = op.get_bind()
    routings = bind.execute(
        sa.text("SELECT COUNT(*) FROM group_contact_center WHERE task_kind <> 'form'")
    ).scalar_one()
    tasks = bind.execute(
        sa.text("SELECT COUNT(*) FROM bookings WHERE contact_center_task_kind = 'conversation'")
    ).scalar_one()
    if routings or tasks:
        raise RuntimeError(
            "conversation routings or tasks exist; they would be read as work items without "
            "their kind"
        )
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("contact_center_task_kind")
    with op.batch_alter_table("group_contact_center") as batch:
        batch.drop_column("task_kind")
