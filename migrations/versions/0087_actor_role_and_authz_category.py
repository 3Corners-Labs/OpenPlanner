"""The actor's role on audit events, and the ``authz`` category for log destinations (ADR 0103).

``audit_events`` gains ``actor_role`` (empty by default): the role key that granted the admin
the permission, for ``actor.role`` on the line and in the CSV export (ADR 0103 point 7). Never a
name; existing rows stay empty.

Authorisation decisions become a category of their own that a log destination ticks (ADR 0103
point 9). Every existing destination received them until now, as ordinary ``info`` lines, so it
gets ``authz`` ticked and nothing disappears silently; a new destination starts without it. The
downgrade drops the column and the tick.

Revises 0086 (booking day counts). Open PRs also claim numbers, so recheck the head before
merging.

Revision ID: 0087
Revises: 0086
Create Date: 2026-11-02 15:00:00.000000
"""

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DESTINATIONS = sa.table(
    "log_destinations", sa.column("id", sa.Integer), sa.column("categories", sa.String)
)


def _rewrite(change: Callable[[set[str]], None]) -> None:
    """Apply ``change`` to the category set of every destination and store it sorted."""
    bind = op.get_bind()
    for row in bind.execute(sa.select(_DESTINATIONS.c.id, _DESTINATIONS.c.categories)).all():
        categories = {part for part in row.categories.split(",") if part}
        change(categories)
        bind.execute(
            _DESTINATIONS.update()
            .where(_DESTINATIONS.c.id == row.id)
            .values(categories=",".join(sorted(categories)))
        )


def upgrade() -> None:
    """Add ``actor_role`` and tick ``authz`` on every existing destination."""
    with op.batch_alter_table("audit_events") as batch:
        batch.add_column(
            sa.Column("actor_role", sa.String(length=64), server_default="", nullable=False)
        )
    _rewrite(lambda categories: categories.add("authz"))


def downgrade() -> None:
    """Untick ``authz`` and drop ``actor_role``."""
    _rewrite(lambda categories: categories.discard("authz"))
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_column("actor_role")
