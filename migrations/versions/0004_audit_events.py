"""Audit events (ADR 0014): logins, denied decisions and changes; no personal data.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-23 19:55:01.428048
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), server_default=sa.text("('')"), nullable=False),
        sa.Column("organisation", sa.String(length=64), nullable=True),
        sa.Column("group", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('success', 'denied', 'failure')", name=op.f("ck_audit_events_outcome")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index("ix_audit_events_at", ["at"], unique=False)
        batch_op.create_index("ix_audit_events_organisation", ["organisation"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_events_organisation")
        batch_op.drop_index("ix_audit_events_at")

    op.drop_table("audit_events")
