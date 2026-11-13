"""Contact-center attempts on work items (ADR 0121 step 6b).

Expand only (ADR 0116): ``work_items.channel`` (``own_queue`` by default, every existing row is
the own queue's), the state list widened by the attempt states ``dispatching``, ``dispatched``,
``reconciliation_required``, ``fenced`` and ``failed``, and the one-active index
``uq_work_items_active`` rebuilt over the new active states, so the own queue and the contact
center can never both hold a request. Only a non-legacy flow writes an attempt, and the rollback
gate keeps every such flow shut (ADR 0122 decision 12); the previous image never writes one.

SQLite changes a check constraint by copying the table; the partial indexes are dropped first and
made again afterwards, so their predicates survive the copy.

Downgrade refuses while a contact-center attempt exists: without the column it could not be told
from the own queue's work.

Revision ID: 0114
Revises: 0113
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0114"
down_revision: str | None = "0113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATES = (
    "'assigned', 'in_progress', 'recovery_required', 'completed', 'released', "
    "'lease_expired', 'expired', 'cancelled'"
)
NEW_STATES = "'dispatching', 'dispatched', 'reconciliation_required', 'fenced', 'failed'"
OLD_ACTIVE = "state IN ('assigned', 'in_progress', 'recovery_required')"
NEW_ACTIVE = (
    "state IN ('assigned', 'in_progress', 'recovery_required', 'dispatching', 'dispatched', "
    "'reconciliation_required')"
)
CHANNELS = "('own_queue', 'contact_center')"


def _drop_indexes() -> None:
    """Drop the indexes of ``work_items`` before the table is copied."""
    op.drop_index("ix_work_items_pool_open", table_name="work_items")
    op.drop_index("uq_work_items_active", table_name="work_items")
    op.drop_index("ix_work_items_request_id", table_name="work_items")


def _make_indexes(active: str) -> None:
    """Make the indexes of ``work_items`` again; ``active`` is the one-active predicate."""
    op.create_index("ix_work_items_request_id", "work_items", ["request_id"])
    op.create_index(
        "uq_work_items_active",
        "work_items",
        ["request_id"],
        unique=True,
        sqlite_where=sa.text(active),
    )
    op.create_index(
        "ix_work_items_pool_open",
        "work_items",
        ["pool_id", "started_at"],
        sqlite_where=sa.text("ended_at IS NULL AND pool_id IS NOT NULL"),
    )


def upgrade() -> None:
    """Add the channel, widen the states and rebuild the one-active index."""
    _drop_indexes()
    with op.batch_alter_table("work_items") as batch:
        batch.add_column(
            sa.Column(
                "channel",
                sa.String(length=16),
                server_default=sa.text("'own_queue'"),
                nullable=False,
            )
        )
        batch.drop_constraint(op.f("ck_work_items_state"), type_="check")
        batch.create_check_constraint(
            op.f("ck_work_items_state"), f"state IN ({OLD_STATES}, {NEW_STATES})"
        )
        batch.create_check_constraint(op.f("ck_work_items_channel"), f"channel IN {CHANNELS}")
    _make_indexes(NEW_ACTIVE)


def downgrade() -> None:
    """Drop the channel and the attempt states; refuse while a contact-center attempt exists."""
    held = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM work_items WHERE channel = 'contact_center'")
    )
    if held.scalar_one():
        raise RuntimeError(
            "contact-center attempts exist; they cannot be told from the own queue's work"
        )
    _drop_indexes()
    with op.batch_alter_table("work_items") as batch:
        batch.drop_constraint(op.f("ck_work_items_channel"), type_="check")
        batch.drop_constraint(op.f("ck_work_items_state"), type_="check")
        batch.create_check_constraint(op.f("ck_work_items_state"), f"state IN ({OLD_STATES})")
        batch.drop_column("channel")
    _make_indexes(OLD_ACTIVE)
