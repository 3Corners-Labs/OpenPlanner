"""E-mail domains claimed by organisation identity providers (ADR 0083 step 3).

``identity_provider_domains`` holds one row per claimed domain: the provider that claims it,
the domain (unique on the installation, point 11), the random token the TXT record must carry,
its state (pending, verified, failing, lapsed) and when it was verified, last checked and first
found missing. A domain is configuration, not personal data; the token is public by design.

Revises 0087 (actor role and authz category, ADR 0103). Open PRs also claim numbers, so recheck
the head before merging.

Revision ID: 0088
Revises: 0087
Create Date: 2026-11-02 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``identity_provider_domains``."""
    op.create_table(
        "identity_provider_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'verified', 'failing', 'lapsed')",
            name=op.f("ck_identity_provider_domains_state"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identity_providers.id"],
            name=op.f("fk_identity_provider_domains_provider_id_identity_providers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_provider_domains")),
        sa.UniqueConstraint("domain", name=op.f("uq_identity_provider_domains_domain")),
    )
    op.create_index(
        op.f("ix_identity_provider_domains_provider_id"),
        "identity_provider_domains",
        ["provider_id"],
    )


def downgrade() -> None:
    """Drop ``identity_provider_domains``."""
    op.drop_index(
        op.f("ix_identity_provider_domains_provider_id"), table_name="identity_provider_domains"
    )
    op.drop_table("identity_provider_domains")
