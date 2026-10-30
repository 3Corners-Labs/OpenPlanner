"""The own certificate uploaded in the portal (ADR 0066).

``own_certificates`` holds one row: the PEM chain (public), the private key sealed with
AES-256-GCM under ``SETTINGS_ENCRYPTION_KEY`` (ADR 0008), and the public data the page shows.
No personal data. The downgrade drops the table; the pair must be uploaded again afterwards.
Revises 0055 (certificate checks, ADR 0064); renumber when another migration lands first.

Revision ID: 0057
Revises: 0055
Create Date: 2026-10-30 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``own_certificates``."""
    op.create_table(
        "own_certificates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chain_pem", sa.Text(), nullable=False),
        sa.Column("key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("not_before", sa.DateTime(), nullable=False),
        sa.Column("not_after", sa.DateTime(), nullable=False),
        sa.Column("names", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_own_certificates_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_own_certificates")),
    )


def downgrade() -> None:
    """Drop ``own_certificates``."""
    op.drop_table("own_certificates")
