"""Display links of the wallboard (ADR 0117 step 14).

One new table, expand only (ADR 0116): the previous image neither reads nor writes it.

- ``wallboard_links``: a display link per screen. Only ``sha256`` of the token and of the
  session secret are stored; the ACL and the pinned network are ranges of staff equipment and
  go with the row. A link belongs to an organisation or one group and goes with it.

Open PRs also claim numbers, so recheck the head before merging.

Revision ID: 0108
Revises: 0107
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0108"
down_revision: str | None = "0107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``wallboard_links``."""
    op.create_table(
        "wallboard_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("session_hash", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("networks", sa.String(length=1500), server_default="", nullable=False),
        sa.Column("pin", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("pinned_network", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_wallboard_links_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_wallboard_links_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallboard_links")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_wallboard_links_token_hash")),
    )
    op.create_index("ix_wallboard_links_organisation_id", "wallboard_links", ["organisation_id"])
    op.create_index("ix_wallboard_links_session_hash", "wallboard_links", ["session_hash"])


def downgrade() -> None:
    """Drop ``wallboard_links``."""
    op.drop_index("ix_wallboard_links_session_hash", table_name="wallboard_links")
    op.drop_index("ix_wallboard_links_organisation_id", table_name="wallboard_links")
    op.drop_table("wallboard_links")
