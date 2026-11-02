"""The installation's id and the latest round trip to the public address (ADR 0086 step 1).

``instance`` holds one row: the installation's random id (made at first use, never edited) and
the hash of the one pending probe nonce. ``public_address_checks`` holds the latest round trip
per address, ``public`` or ``webhook``. No personal data. The downgrade drops both tables; a
new id is made at the next start. Revises 0083 (participant link sessions, ADR 0093). Open PRs
also claim numbers, so recheck the head before merging.

Revision ID: 0084
Revises: 0083
Create Date: 2026-11-02 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``instance`` and ``public_address_checks``."""
    op.create_table(
        "instance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("probe_nonce_hash", sa.String(length=64), nullable=True),
        sa.Column("probe_nonce_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_instance_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instance")),
    )
    op.create_table(
        "public_address_checks",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("url", sa.String(length=255), nullable=False),
        sa.Column("finding", sa.String(length=24), nullable=False),
        sa.Column("scheme", sa.String(length=8), nullable=True),
        sa.Column("forwarded", sa.String(length=12), nullable=True),
        sa.Column("error", sa.String(length=40), server_default=sa.text("''"), nullable=False),
        sa.PrimaryKeyConstraint("kind", name=op.f("pk_public_address_checks")),
    )


def downgrade() -> None:
    """Drop both tables."""
    op.drop_table("public_address_checks")
    op.drop_table("instance")
