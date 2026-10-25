"""Call tracking: booking timeline, aggregated call status, lookup indexes (ADR 0010, 0017, 0018).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-24 00:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "booking_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=128), server_default=sa.text("('')"), nullable=False),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_booking_events_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_events")),
    )
    with op.batch_alter_table("booking_events", schema=None) as batch_op:
        batch_op.create_index("ix_booking_events_booking_id", ["booking_id"], unique=False)
    op.create_table(
        "call_status",
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("call", sa.String(length=16), nullable=False),
        sa.Column("task", sa.String(length=16), nullable=False),
        sa.Column("participants", sa.Integer(), nullable=False),
        sa.Column("host_joined", sa.Boolean(), nullable=False),
        sa.Column("host_joined_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("task_created_at", sa.DateTime(), nullable=True),
        sa.Column("connected_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_call_status_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("booking_id", name=op.f("pk_call_status")),
    )
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.create_index("ix_bookings_video_room_id", ["video_room_id"], unique=False)
        batch_op.create_index("ix_bookings_wxcc_task_id", ["wxcc_task_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_index("ix_bookings_wxcc_task_id")
        batch_op.drop_index("ix_bookings_video_room_id")
    op.drop_table("call_status")
    with op.batch_alter_table("booking_events", schema=None) as batch_op:
        batch_op.drop_index("ix_booking_events_booking_id")
    op.drop_table("booking_events")
