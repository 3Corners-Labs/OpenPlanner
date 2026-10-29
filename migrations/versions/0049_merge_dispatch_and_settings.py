"""Join the two heads 0042 (dispatch kind, ADR 0043) and 0048 (platform settings, ADR 0042).

Both revised 0047 and were merged on the same day, so the history forked. This revision has
no schema change of its own; it only makes ``alembic upgrade head`` one path again. Merged
migrations stay as they are.

Revision ID: 0049
Revises: 0042, 0048
Create Date: 2026-10-29 19:30:00.000000
"""

from collections.abc import Sequence

revision: str = "0049"
down_revision: str | tuple[str, ...] | None = ("0042", "0048")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to change: both branches are already applied."""


def downgrade() -> None:
    """Nothing to change: going down splits into the two branches again."""
