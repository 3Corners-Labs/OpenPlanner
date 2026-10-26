"""Machine API (ADR 0023): switches, API clients, their groups and access tokens.

Secrets and tokens are stored as SHA-256 only. A client's groups use ``RESTRICT``: removing a
group must never silently widen a client narrowed to it to the whole organisation.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-25 23:40:13.182474
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``api_settings``, ``api_clients``, ``api_client_groups`` and tokens."""
    op.create_table(
        "api_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("docs_public", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_api_settings_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_settings")),
    )
    op.create_table(
        "api_clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=False),
        sa.Column(
            "networks", sa.String(length=1024), server_default=sa.text("('')"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("secret_expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_token_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_api_clients_organisation_id_organisations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_clients")),
        sa.UniqueConstraint("client_id", name=op.f("uq_api_clients_client_id")),
    )
    with op.batch_alter_table("api_clients", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_api_clients_organisation_id"), ["organisation_id"], unique=False
        )

    op.create_table(
        "api_access_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("client_pk", sa.Integer(), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_pk"],
            ["api_clients.id"],
            name=op.f("fk_api_access_tokens_client_pk_api_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_api_access_tokens")),
    )
    with op.batch_alter_table("api_access_tokens", schema=None) as batch_op:
        batch_op.create_index("ix_api_access_tokens_client_pk", ["client_pk"], unique=False)
        batch_op.create_index("ix_api_access_tokens_expires_at", ["expires_at"], unique=False)

    op.create_table(
        "api_client_groups",
        sa.Column("client_pk", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_pk"],
            ["api_clients.id"],
            name=op.f("fk_api_client_groups_client_pk_api_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_api_client_groups_group_id_groups"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("client_pk", "group_id", name=op.f("pk_api_client_groups")),
    )


def downgrade() -> None:
    """Drop the machine API tables; registered clients and live tokens are lost."""
    op.drop_table("api_client_groups")
    with op.batch_alter_table("api_access_tokens", schema=None) as batch_op:
        batch_op.drop_index("ix_api_access_tokens_expires_at")
        batch_op.drop_index("ix_api_access_tokens_client_pk")

    op.drop_table("api_access_tokens")
    with op.batch_alter_table("api_clients", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_clients_organisation_id"))

    op.drop_table("api_clients")
    op.drop_table("api_settings")
