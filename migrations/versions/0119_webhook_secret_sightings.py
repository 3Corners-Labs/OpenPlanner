"""The first sighting of each installation webhook secret (ADR 0124 point 8).

Expand only (ADR 0116): one new table, nothing existing is touched. The previous image never reads
or writes it, so it keeps working on this schema and a restore into it loses nothing it knew.

* ``webhook_secret_sightings``: one row per platform (``webex``, ``wxcc``): a keyed fingerprint of
  the secret in the environment (an HMAC, not reversible, never the secret) and when the
  application first saw it. A previous value of the secret verifies for the overlap counted from
  that moment, so a restart does not start the overlap again. Empty until the first webhook
  address is called after the upgrade.

Downgrade drops the table. Nothing depends on it: the first sighting is made again by the next
call, which at worst starts the overlap of a previous value in the environment again.

Revision ID: 0119
Revises: 0118
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0119"
down_revision: str | None = "0118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table of first sightings."""
    op.create_table(
        "webhook_secret_sightings",
        sa.Column("platform", sa.String(length=16), primary_key=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("first_seen", UTCDateTime(), nullable=False),
    )


def downgrade() -> None:
    """Drop the table."""
    op.drop_table("webhook_secret_sightings")
