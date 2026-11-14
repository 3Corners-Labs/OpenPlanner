"""The screen name of a staff member for the participant's side of a call (ADR 0123 step 5c).

Expand only (ADR 0116): one nullable column on ``staff``, no default, so no row is written. The
previous image never reads or writes it, so it keeps working on this schema and a restore into it
loses nothing it knew.

* ``staff.screen_name``: the name the participant of a call in our own page sees for the staff
  member, set by someone with ``staff.manage`` on the staff member's page. NULL for everyone
  until it is set. Personal data of the staff member: erasing the record (ADR 0046) clears it,
  and neither an audit event nor a log line carries it.

Downgrade drops the column; the names are lost, which is what the previous image never had.

Revision ID: 0118
Revises: 0117
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0118"
down_revision: str | None = "0117"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable screen name."""
    with op.batch_alter_table("staff") as batch:
        batch.add_column(sa.Column("screen_name", sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Drop the column."""
    with op.batch_alter_table("staff") as batch:
        batch.drop_column("screen_name")
