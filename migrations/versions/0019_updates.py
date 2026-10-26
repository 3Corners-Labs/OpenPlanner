"""Updates started from the admin portal (ADR 0028): release checks and update requests.

``release_checks`` holds one row with the outcome of the worker's hourly check;
``update_requests`` the superuser's requests and what the host updater reported. Neither holds
personal data. The downgrade drops both; an update in progress is then forgotten, which is what
a restore to a version without this feature means.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-26 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``release_checks`` and ``update_requests``."""
    op.create_table(
        "release_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("latest_version", sa.String(length=20), nullable=True),
        sa.Column("release_url", sa.String(length=300), nullable=True),
        sa.Column("error", sa.String(length=40), server_default=sa.text("''"), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_release_checks_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_release_checks")),
    )
    op.create_table(
        "update_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("target_version", sa.String(length=20), nullable=False),
        sa.Column("from_version", sa.String(length=20), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.String(length=40), server_default=sa.text("''"), nullable=False),
        sa.CheckConstraint(
            "action IN ('update', 'restore')", name=op.f("ck_update_requests_action")
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'running', 'succeeded', 'rolled_back', 'failed', 'cancelled')",
            name=op.f("ck_update_requests_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_update_requests")),
    )
    op.create_index("ix_update_requests_status", "update_requests", ["status"], unique=False)


def downgrade() -> None:
    """Drop both tables."""
    op.drop_index("ix_update_requests_status", table_name="update_requests")
    op.drop_table("update_requests")
    op.drop_table("release_checks")
