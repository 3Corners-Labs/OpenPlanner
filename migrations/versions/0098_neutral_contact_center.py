"""Provider-neutral contact-center routing and task ids (ADR 0032 steps CC-1/CC-2).

The expand step of ADR 0116, not the single renaming migration ADR 0032 sketched:

- creates ``group_contact_center`` (group, kind, routing value, attributes as a JSON object)
  and copies every group's ``wxcc_*`` routing into it with kind ``webex_cc``;
- adds the nullable ``bookings.contact_center_task_id`` and ``bookings.contact_center_kind``
  and copies ``wxcc_task_id`` into them;

The stored job type ``dispatch_wxcc_task`` is not touched: the previous minor's image must keep
reading it (ADR 0116 §5); the next minor switches the value, the one after drops the old one.

The old columns stay and stay the source of truth for a Webex Contact Center routing or task,
so the previous image keeps working and the downgrade drops the new table and columns without
losing anything. Dropping the old columns is the contract step, a minor later.

Revision ID: 0098
Revises: 0097
Create Date: 2026-11-08 20:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0098"
down_revision: str | None = "0097"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WEBEX_CC = "webex_cc"


def upgrade() -> None:
    """Create the routing table and task columns and copy the WxCC values into them."""
    op.create_table(
        "group_contact_center",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("routing_value", sa.String(512), nullable=True),
        sa.Column("attributes", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint(
            "json_valid(attributes) AND json_type(attributes) = 'object'",
            name=op.f("ck_group_contact_center_attributes"),
        ),
    )
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("contact_center_task_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("contact_center_kind", sa.String(32), nullable=True))
        batch.create_index("ix_bookings_contact_center_task_id", ["contact_center_task_id"])

    connection = op.get_bind()
    groups = connection.execute(
        sa.text(
            "SELECT id, wxcc_business_address, wxcc_channel, wxcc_routing_value FROM groups "
            "WHERE wxcc_business_address IS NOT NULL OR wxcc_channel IS NOT NULL "
            "OR wxcc_routing_value IS NOT NULL"
        )
    ).fetchall()
    for group_id, address, channel, routing_value in groups:
        attributes = {
            name: value
            for name, value in (("business_address", address), ("channel", channel))
            if value is not None
        }
        connection.execute(
            sa.text(
                "INSERT INTO group_contact_center (group_id, kind, routing_value, attributes) "
                "VALUES (:group_id, :kind, :routing_value, :attributes)"
            ),
            {
                "group_id": group_id,
                "kind": WEBEX_CC,
                "routing_value": routing_value,
                "attributes": json.dumps(attributes, sort_keys=True, separators=(",", ":")),
            },
        )
    connection.execute(
        sa.text(
            "UPDATE bookings SET contact_center_task_id = wxcc_task_id, "
            "contact_center_kind = :kind WHERE wxcc_task_id IS NOT NULL"
        ),
        {"kind": WEBEX_CC},
    )


def downgrade() -> None:
    """Drop the new table and columns (the old ones hold it all)."""
    with op.batch_alter_table("bookings") as batch:
        batch.drop_index("ix_bookings_contact_center_task_id")
        batch.drop_column("contact_center_kind")
        batch.drop_column("contact_center_task_id")
    op.drop_table("group_contact_center")
