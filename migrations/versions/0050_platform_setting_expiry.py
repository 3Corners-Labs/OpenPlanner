"""When a portal setting lapses (ADR 0042 step 4).

``platform_settings`` gains ``expires_at``: a ``LOG_LEVEL`` set in the portal falls back to
the default after at most 24 hours (ADR 0042 §6); every other value has none. The downgrade
drops the column; a stored log level would then stay until reset, so it is removed first.

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-29 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``platform_settings.expires_at``."""
    with op.batch_alter_table("platform_settings") as table:
        table.add_column(sa.Column("expires_at", UTCDateTime(), nullable=True))


def downgrade() -> None:
    """Remove values with an expiry, then the column."""
    op.execute("DELETE FROM platform_settings WHERE expires_at IS NOT NULL")
    with op.batch_alter_table("platform_settings") as table:
        table.drop_column("expires_at")
