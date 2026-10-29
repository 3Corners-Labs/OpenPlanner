"""Join the two heads 0043 (own-queue claims, ADR 0043) and 0051 (organisation settings, ADR 0042).

Both were merged minutes apart on 2026-10-29: 0043 revises 0042, 0051 revises 0050, and both
lines start from the merge revision 0049. This revision has no schema change of its own; it
only makes ``alembic upgrade head`` one path again. Merged migrations stay as they are.

Revision ID: 0052
Revises: 0043, 0051
Create Date: 2026-10-29 22:30:00.000000
"""

from collections.abc import Sequence

revision: str = "0052"
down_revision: str | tuple[str, ...] | None = ("0043", "0051")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to change: both branches are already applied."""


def downgrade() -> None:
    """Nothing to change: going down splits into the two branches again."""
