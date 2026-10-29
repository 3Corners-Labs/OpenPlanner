"""Merge the two heads 0026 (staff directory) and 0027 (update steps).

Both were written against 0025 in parallel branches (ADR 0046 step 1 in #61, ADR 0048 in #63)
and merged one after the other, which left ``main`` with two heads. Merged migrations are
never edited (CLAUDE.md), so this revision joins them. It changes nothing in the schema;
migrations written after it revise ``0038``.

Revision ID: 0038
Revises: 0026, 0027
Create Date: 2026-10-29 10:00:00.000000
"""

from collections.abc import Sequence

revision: str = "0038"
down_revision: str | Sequence[str] | None = ("0026", "0027")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the two heads; no schema change."""


def downgrade() -> None:
    """Split back into the two heads; no schema change."""
