"""MFA policy per organisation identity provider (ADR 0100 step 3).

``identity_providers`` gets ``mfa_policy`` (``off``, ``record`` or ``require``; existing
providers become ``record``, which only records a login without a second factor), and the
``amr`` and ``acr`` values that count as proof. No personal data.

Revises 0081 (admin sessions, ADR 0098). Open PRs also claim numbers, so recheck
the head before merging.

Revision ID: 0082
Revises: 0081
Create Date: 2026-11-01 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the three columns and the check on the policy."""
    with op.batch_alter_table("identity_providers") as batch:
        batch.add_column(
            sa.Column("mfa_policy", sa.String(length=16), server_default="record", nullable=False)
        )
        batch.add_column(
            sa.Column("mfa_amr_values", sa.String(length=1100), server_default="", nullable=False)
        )
        batch.add_column(
            sa.Column("mfa_acr_values", sa.String(length=1100), server_default="", nullable=False)
        )
        batch.create_check_constraint(
            op.f("ck_identity_providers_mfa_policy"),
            "mfa_policy IN ('off', 'record', 'require')",
        )


def downgrade() -> None:
    """Drop the check and the three columns."""
    with op.batch_alter_table("identity_providers") as batch:
        batch.drop_constraint(op.f("ck_identity_providers_mfa_policy"), type_="check")
        batch.drop_column("mfa_acr_values")
        batch.drop_column("mfa_amr_values")
        batch.drop_column("mfa_policy")
