"""The own certificate uploaded in the portal (ADR 0070).

``own_certificates`` holds one row: the PEM chain (public), the leaf's public key and the
public data the page shows. Never a private key (it lives on the volume the proxy reads) and no
personal data. The downgrade drops the table; the chain must be uploaded again afterwards.
Revises 0059 (booking event version), the head of main when this landed. Parallel PRs add
migrations too: when the merge queue reports two heads, only this line (and the two below)
needs to follow the new head.

Revision ID: 0061
Revises: 0059
Create Date: 2026-10-30 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``own_certificates``."""
    op.create_table(
        "own_certificates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chain_pem", sa.Text(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
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
