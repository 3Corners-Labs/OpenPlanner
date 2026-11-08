"""When a request may be claimed, and the support index of the release check (ADR 0116 step 5).

Expand only (ADR 0116 point 5): two nullable columns. ``update_requests.apply_after`` holds the
moment before which the host updater does not take a request (a superuser may schedule one);
``release_checks.support`` keeps the last ``support.json`` the worker read, so the updates
page shows the running line's support status. The downgrade drops both; no data elsewhere
depends on them.

Revises 0091 (subjects and routing); open PRs also claim numbers, so recheck
the head before merging.

Revision ID: 0092
Revises: 0091
Create Date: 2026-11-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``update_requests.apply_after`` and ``release_checks.support``."""
    with op.batch_alter_table("update_requests") as batch:
        batch.add_column(sa.Column("apply_after", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("release_checks") as batch:
        batch.add_column(sa.Column("support", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop both columns again."""
    with op.batch_alter_table("release_checks") as batch:
        batch.drop_column("support")
    with op.batch_alter_table("update_requests") as batch:
        batch.drop_column("apply_after")
