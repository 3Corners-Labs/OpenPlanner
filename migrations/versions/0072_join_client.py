"""Which Webex page the citizen's join link opens, per group (ADR 0073).

``groups`` gains ``join_client`` (``browser`` or ``choice``, default ``browser``): with
``browser`` the link carries Webex's own parameter for the web app, with ``choice`` it is the
stored link unchanged. Existing groups get ``browser``, the ADR's default.

Revises 0070 (running versions). PR #148 claims 0071 (release listing): when it merges first,
change ``down_revision`` to ``0071`` before merging this one, so there is a single head.

Revision ID: 0072
Revises: 0070
Create Date: 2026-10-30 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the group setting with its check."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "join_client",
                sa.String(length=16),
                server_default=sa.text("'browser'"),
                nullable=False,
            )
        )
        batch.create_check_constraint("join_client", "join_client IN ('browser', 'choice')")


def downgrade() -> None:
    """Drop the setting again; every group then gets Webex's own page."""
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("join_client", type_="check")
        batch.drop_column("join_client")
