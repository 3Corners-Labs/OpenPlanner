"""Portal values of the platform settings and their version counter (ADR 0042 step 3).

``platform_settings`` holds one JSON value per class C setting that a superuser set in the
portal (step 4 writes them); secrets never go here but sealed in ``integration_credentials``
(ADR 0008). ``settings_version`` holds at most one row with the counter that web and worker
watch to reload (ADR 0042 §6). No personal data. The downgrade drops both tables; every
setting then comes from the environment or its default again, as before this feature.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-29 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``platform_settings`` and ``settings_version``."""
    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_platform_settings")),
    )
    op.create_table(
        "settings_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_settings_version_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settings_version")),
    )


def downgrade() -> None:
    """Drop ``settings_version`` and ``platform_settings``."""
    op.drop_table("settings_version")
    op.drop_table("platform_settings")
