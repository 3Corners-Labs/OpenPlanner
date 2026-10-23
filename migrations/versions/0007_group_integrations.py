"""Group integrations: Webex host and WxCC work item mapping replace the queue id (ADR 0017/0018).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-24 00:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.add_column(sa.Column("video_host_email", sa.String(length=254), nullable=True))
        batch_op.add_column(
            sa.Column("wxcc_business_address", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(sa.Column("wxcc_channel", sa.String(length=255), nullable=True))
        # The old queue id becomes the routing value the WxCC flow chooses the queue on.
        batch_op.alter_column(
            "wxcc_queue_id",
            new_column_name="wxcc_routing_value",
            existing_type=sa.String(length=128),
            type_=sa.String(length=512),
            existing_nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "direct_call_enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False
            )
        )
        batch_op.add_column(
            sa.Column(
                "not_picked_up_minutes", sa.Integer(), server_default=sa.text("10"), nullable=False
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.drop_column("not_picked_up_minutes")
        batch_op.drop_column("direct_call_enabled")
        batch_op.alter_column(
            "wxcc_routing_value",
            new_column_name="wxcc_queue_id",
            existing_type=sa.String(length=512),
            type_=sa.String(length=128),
            existing_nullable=True,
        )
        batch_op.drop_column("wxcc_channel")
        batch_op.drop_column("wxcc_business_address")
        batch_op.drop_column("video_host_email")
