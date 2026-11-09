"""The verified email address of a staff member, read at login (ADR 0117 step 10b).

Expand only (ADR 0116): two nullable columns on ``staff``; the previous image ignores them.

- ``email``: the ``email`` claim of the last login, kept only when the provider said
  ``email_verified`` is true, otherwise ``NULL``. It is personal data: the digest of the usage
  reports reads it, the staff page shows it, and erasing the person clears it.
- ``email_at``: when it was last read.

Open PRs also claim numbers, so recheck the head before merging.

Revision ID: 0104
Revises: 0103
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0104"
down_revision: str | None = "0103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``email`` and ``email_at`` to ``staff``."""
    with op.batch_alter_table("staff") as batch:
        batch.add_column(sa.Column("email", sa.String(length=320), nullable=True))
        batch.add_column(sa.Column("email_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop ``email`` and ``email_at``."""
    with op.batch_alter_table("staff") as batch:
        batch.drop_column("email_at")
        batch.drop_column("email")
