"""Join the two heads 0055 (certificate checks, ADR 0064) and 0056 (event messages, ADR 0065).

Both revise 0054 and were merged hours apart on 2026-10-30. This revision has no schema change
of its own; it only makes ``alembic upgrade head`` one path again. Merged migrations stay as
they are.

Revision ID: 0057
Revises: 0055, 0056
Create Date: 2026-10-30 16:30:00.000000
"""

from collections.abc import Sequence

revision: str = "0057"
down_revision: str | tuple[str, ...] | None = ("0055", "0056")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to change: both branches are already applied."""


def downgrade() -> None:
    """Nothing to change: going down splits into the two branches again."""
