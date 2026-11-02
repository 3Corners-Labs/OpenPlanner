"""An index on the action and time of audit events, for the open alerts (ADR 0061 §7).

Every admin page reads the newest system events of a few condition actions to show the alert
banner; without an index on ``action`` that is a scan of every system event. No data changes,
so the downgrade only drops the index.

Revises 0088 (identity provider domains). Open PRs also claim numbers, so recheck the head before
merging.

Revision ID: 0089
Revises: 0088
Create Date: 2026-11-02 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0089"
down_revision: str | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``ix_audit_events_action_at``."""
    op.create_index("ix_audit_events_action_at", "audit_events", ["action", "at"])


def downgrade() -> None:
    """Drop ``ix_audit_events_action_at``."""
    op.drop_index("ix_audit_events_action_at", table_name="audit_events")
