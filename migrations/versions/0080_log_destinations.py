"""Log destinations and their delivery status (ADR 0085 step 2).

``log_destinations`` holds one row per place the log is copied to: kind, address, the
filters of what it receives and the sealed credential (ADR 0008). ``log_destination_status``
holds what each process (``web``, ``worker``) reports per destination: counters and a machine
reason, nothing a platform answered and no line. No personal data in either.

Revises 0079 (identity providers, ADR 0083). Open PRs also claim numbers, so recheck the head
before merging.

Revision ID: 0080
Revises: 0079
Create Date: 2026-11-01 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``log_destinations`` and ``log_destination_status``."""
    op.create_table(
        "log_destinations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("index_name", sa.String(length=120), server_default="", nullable=False),
        sa.Column("source", sa.String(length=120), server_default="", nullable=False),
        sa.Column("min_level", sa.String(length=16), nullable=False),
        sa.Column("categories", sa.String(length=64), nullable=False),
        sa.Column("include_diagnostic", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("include_trace", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("ca_pem", sa.Text(), server_default="", nullable=False),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('syslog', 'splunk_hec', 'elastic', 'http')",
            name=op.f("ck_log_destinations_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_log_destinations")),
        sa.UniqueConstraint("name", name=op.f("uq_log_destinations_name")),
    )
    op.create_table(
        "log_destination_status",
        sa.Column("destination_id", sa.Integer(), nullable=False),
        sa.Column("service", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("sent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dropped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("queued", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=16), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["log_destinations.id"],
            name=op.f("fk_log_destination_status_destination_id_log_destinations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "destination_id", "service", name=op.f("pk_log_destination_status")
        ),
    )


def downgrade() -> None:
    """Drop both tables."""
    op.drop_table("log_destination_status")
    op.drop_table("log_destinations")
