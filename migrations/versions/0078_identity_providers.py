"""Organisation identity providers and their signing keys (ADR 0083 step 2).

``identity_providers`` holds one registration per organisation provider: discovery address,
issuer, client id, authentication method, scopes, entitlement claim and prefix, the on/off
switch and, for ``client_secret_basic``, the sealed client secret (ADR 0008).
``identity_provider_keys`` holds the key pairs of ``private_key_jwt``: the public JWK in the
clear, the private key sealed, and when a rotation retired it. No personal data in either.
The domains of step 3 get their own migration.

Revises 0077 (participant change lead). Open PRs also claim numbers, so recheck the head
before merging.

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-31 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``identity_providers`` and ``identity_provider_keys``."""
    op.create_table(
        "identity_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("discovery_url", sa.String(length=500), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_auth", sa.String(length=32), nullable=False),
        sa.Column("scopes", sa.String(length=200), nullable=False),
        sa.Column("entitlement_claim", sa.String(length=64), nullable=False),
        sa.Column("entitlement_prefix", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "client_auth IN ('private_key_jwt', 'client_secret_basic')",
            name=op.f("ck_identity_providers_client_auth"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_identity_providers_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_providers")),
        sa.UniqueConstraint("key", name=op.f("uq_identity_providers_key")),
    )
    op.create_index(
        op.f("ix_identity_providers_organisation_id"),
        "identity_providers",
        ["organisation_id"],
    )
    op.create_table(
        "identity_provider_keys",
        sa.Column("kid", sa.String(length=64), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("public_jwk", sa.Text(), nullable=False),
        sa.Column("key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identity_providers.id"],
            name=op.f("fk_identity_provider_keys_provider_id_identity_providers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("kid", name=op.f("pk_identity_provider_keys")),
    )
    op.create_index(
        op.f("ix_identity_provider_keys_provider_id"), "identity_provider_keys", ["provider_id"]
    )


def downgrade() -> None:
    """Drop both tables; registrations and keys are lost, the platform provider stays."""
    op.drop_index(
        op.f("ix_identity_provider_keys_provider_id"), table_name="identity_provider_keys"
    )
    op.drop_table("identity_provider_keys")
    op.drop_index(op.f("ix_identity_providers_organisation_id"), table_name="identity_providers")
    op.drop_table("identity_providers")
