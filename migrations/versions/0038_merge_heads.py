"""Merge the two heads 0027 (update steps) and 0029 (maintenance state).

``0026_staff_directory`` (#61), ``0027_update_steps`` (#63) and ``0029_maintenance_state``
(#65, ADR 0038) were written in parallel and merged one after the other: 0026 first, then 0027
and 0029 both on top of it, which left ``main`` with two heads. Merged migrations are never
edited (CLAUDE.md), so this revision joins them. It changes nothing in the schema; migrations
written after it revise ``0038``.

Revision ID: 0038
Revises: 0027, 0029
Create Date: 2026-10-29 10:00:00.000000
"""

from collections.abc import Sequence

revision: str = "0038"
down_revision: str | Sequence[str] | None = ("0027", "0029")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the two heads; no schema change."""


def downgrade() -> None:
    """Split back into the two heads; no schema change."""
