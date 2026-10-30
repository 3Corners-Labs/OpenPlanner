"""The latest check of the public address's certificate (ADR 0064).

``certificate_checks`` holds one row: when the worker (or the CLI) last probed the host of
``PUBLIC_BASE_URL``, what it found and the public data of the certificate it saw. No personal
data. The downgrade drops the table; the next worker round writes it again. Revises 0054
(bookings per email address, ADR 0059), merged first.

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-30 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``certificate_checks``."""
    op.create_table(
        "certificate_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("finding", sa.String(length=16), nullable=False),
        sa.Column("trust", sa.String(length=16), nullable=True),
        sa.Column("error", sa.String(length=40), server_default=sa.text("''"), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("issuer", sa.String(length=255), nullable=True),
        sa.Column("not_before", sa.DateTime(), nullable=True),
        sa.Column("not_after", sa.DateTime(), nullable=True),
        sa.Column("names", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_certificate_checks_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_checks")),
    )


def downgrade() -> None:
    """Drop ``certificate_checks``."""
    op.drop_table("certificate_checks")
