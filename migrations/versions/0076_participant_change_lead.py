"""Rename the participant change lead of a group (ADR 0077).

``groups.citizen_change_lead_minutes`` becomes ``participant_change_lead_minutes`` and its check
constraint ``citizen_lead`` becomes ``participant_lead``; values are kept. The downgrade renames
both back. Audit events already written keep the old field name in their detail.

Revises 0075 (join client). Open PRs also claim numbers, so recheck the head
before merging.

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-31 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename the column and its check constraint."""
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("citizen_lead", type_="check")
        batch.alter_column(
            "citizen_change_lead_minutes", new_column_name="participant_change_lead_minutes"
        )
        batch.create_check_constraint(
            "participant_lead", "participant_change_lead_minutes BETWEEN 0 AND 1440"
        )


def downgrade() -> None:
    """Rename the column and its check constraint back."""
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("participant_lead", type_="check")
        batch.alter_column(
            "participant_change_lead_minutes", new_column_name="citizen_change_lead_minutes"
        )
        batch.create_check_constraint(
            "citizen_lead", "citizen_change_lead_minutes BETWEEN 0 AND 1440"
        )
