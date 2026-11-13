"""Requests, work items and the request-level personal data (ADR 0121 step 5).

Expand only (ADR 0116): three new tables, nothing on an existing table changes, nothing is
backfilled. ``service_requests`` is the identity of one request under one flow revision with an
optional booking (unique: one appointment, one request); ``work_items`` are the attempts of staff
to handle it, at most one active (partial unique index ``uq_work_items_active``), and a started
one that has not ended is the unit of a pool it occupies; ``request_personal_data`` holds the
personal data of a request without an appointment (decision D1), with the booking's column names.

The previous image does not know the tables and never writes them; it can still cancel a booking
that has a request, and the new image's watchdog round sees that (design section 3). A booking
the rollup deletes takes its request with it (``ON DELETE CASCADE``).

Downgrade drops the three tables, and refuses while a request-level record holds personal data:
dropping it would delete data outside the retention rules' sight without a trace (the precedent
is migration 0111 refusing while a slot carries a pool). Requests and work items hold none and
go without asking.

Revision ID: 0113
Revises: 0112
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0113"
down_revision: str | None = "0112"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HANDLING = (
    "('queued', 'assigned', 'in_progress', 'recovery_required', 'completed', 'cancelled', "
    "'expired')"
)
ITEM_STATES = (
    "('assigned', 'in_progress', 'recovery_required', 'completed', 'released', "
    "'lease_expired', 'expired', 'cancelled')"
)
ACTIVE = "state IN ('assigned', 'in_progress', 'recovery_required')"


def _requests() -> None:
    """Create ``service_requests`` with its keys and checks."""
    op.create_table(
        "service_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("flow_revision", sa.Integer(), nullable=False),
        sa.Column("intake", sa.String(length=16), nullable=False),
        sa.Column("booking_id", sa.Integer(), nullable=True),
        sa.Column("handling", sa.String(length=24), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("watchdog_at", UTCDateTime(), nullable=False),
        sa.Column("resolved_at", UTCDateTime(), nullable=True),
        sa.Column(
            "personal_data",
            sa.String(length=16),
            server_default=sa.text("'not_collected'"),
            nullable=False,
        ),
        sa.Column("data_deleted_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "intake IN ('appointment', 'on_demand')", name=op.f("ck_service_requests_intake")
        ),
        sa.CheckConstraint(f"handling IN {HANDLING}", name=op.f("ck_service_requests_handling")),
        sa.CheckConstraint(
            "personal_data IN ('not_collected', 'present', 'deleted')",
            name=op.f("ck_service_requests_personal_data"),
        ),
        sa.CheckConstraint(
            "(intake = 'appointment') = (booking_id IS NOT NULL)",
            name=op.f("ck_service_requests_appointment_booking"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_service_requests_organisation_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_service_requests_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_service_requests_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["flow_id", "flow_revision"],
            ["flow_revisions.flow_id", "flow_revisions.revision"],
            name=op.f("fk_service_requests_flow_id_flow_revisions"),
            ondelete="NO ACTION",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_requests")),
        sa.UniqueConstraint("booking_id", name=op.f("uq_service_requests_booking_id")),
    )
    op.create_index("ix_service_requests_group_id", "service_requests", ["group_id"])
    op.create_index("ix_service_requests_watchdog_at", "service_requests", ["watchdog_at"])
    op.create_index("ix_service_requests_flow", "service_requests", ["flow_id", "flow_revision"])


def _work_items() -> None:
    """Create ``work_items`` with the one-active index and the open-occupation index."""
    op.create_table(
        "work_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("assigned_to", sa.String(length=64), nullable=True),
        sa.Column("assigned_at", UTCDateTime(), nullable=False),
        sa.Column("lease_until", UTCDateTime(), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("pool_id", sa.Integer(), nullable=True),
        sa.Column("planned_until", UTCDateTime(), nullable=True),
        sa.Column("start_conflict", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("ended_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(f"state IN {ITEM_STATES}", name=op.f("ck_work_items_state")),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["service_requests.id"],
            name=op.f("fk_work_items_request_id_service_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_items")),
    )
    op.create_index("ix_work_items_request_id", "work_items", ["request_id"])
    op.create_index(
        "uq_work_items_active",
        "work_items",
        ["request_id"],
        unique=True,
        sqlite_where=sa.text(ACTIVE),
    )
    op.create_index(
        "ix_work_items_pool_open",
        "work_items",
        ["pool_id", "started_at"],
        sqlite_where=sa.text("ended_at IS NULL AND pool_id IS NOT NULL"),
    )


def upgrade() -> None:
    """Create the three tables."""
    _requests()
    _work_items()
    op.create_table(
        "request_personal_data",
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone", sa.String(length=16), nullable=True),
        sa.Column("form_answers", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["service_requests.id"],
            name=op.f("fk_request_personal_data_request_id_service_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("request_id", name=op.f("pk_request_personal_data")),
    )


def downgrade() -> None:
    """Drop the three tables; refuse while a request-level record holds personal data."""
    held = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM request_personal_data"))
    if held.scalar_one():
        raise RuntimeError(
            "request-level personal data exists; delete it under the retention rules first"
        )
    op.drop_table("request_personal_data")
    op.drop_index("ix_work_items_pool_open", table_name="work_items")
    op.drop_index("uq_work_items_active", table_name="work_items")
    op.drop_index("ix_work_items_request_id", table_name="work_items")
    op.drop_table("work_items")
    op.drop_index("ix_service_requests_flow", table_name="service_requests")
    op.drop_index("ix_service_requests_watchdog_at", table_name="service_requests")
    op.drop_index("ix_service_requests_group_id", table_name="service_requests")
    op.drop_table("service_requests")
