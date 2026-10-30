"""Which Webex page the citizen's join link opens, per group (ADR 0075).

``bookings`` gets an index on ``trial_room_id`` (ADR 0076: Webex events of the trial meeting
are matched on it). ``groups`` gains ``join_client`` (``browser`` or ``choice``, default
``browser``): with ``browser`` the link carries Webex's own parameter for the web app, with
``choice`` it is the stored link unchanged. Existing groups get ``browser``, the ADR's default.

Revises 0073 (release check requested), the head of ``main`` when this was written. PR #168 claims
0072 (organisation texts): when it merges first, set ``down_revision`` to the head of ``main``
before merging this one, so there is a single head (``tests/test_migrations.py`` checks it).

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-30 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the group setting with its check."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column(
                "join_client",
                sa.String(length=16),
                server_default=sa.text("'browser'"),
                nullable=False,
            )
        )
        batch.create_check_constraint("join_client", "join_client IN ('browser', 'choice')")
    # Webex events of the trial meeting are looked up by its id (ADR 0076), like the
    # appointment's own meeting, which has had an index since 0008.
    with op.batch_alter_table("bookings") as batch:
        batch.create_index("ix_bookings_trial_room_id", ["trial_room_id"], unique=False)


def downgrade() -> None:
    """Drop the index and the setting again; every group then gets Webex's own page."""
    with op.batch_alter_table("bookings") as batch:
        batch.drop_index("ix_bookings_trial_room_id")
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint("join_client", type_="check")
        batch.drop_column("join_client")
