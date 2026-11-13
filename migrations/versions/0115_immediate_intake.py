"""Immediate intake and check-in on the same pool (ADR 0121 step 7).

Expand only (ADR 0116), plain ``ALTER TABLE ADD COLUMN`` and new indexes, no table copy:

* ``capacity_pools.max_waiting``: how many immediate requests may wait for the pool at once
  (NULL is no limit; the bounds 1 to 1000 are checked by the adapter);
* ``service_requests.pool_id``, ``subject``, ``arrived_at``, ``public_ref``, ``access_hash`` and
  ``intake_key_hash``: the pool and subject of immediate work, attendance, and how the
  participant finds an immediate request again (hashes only);
* ``uq_service_requests_public_ref``, ``uq_service_requests_intake_key`` (one request per
  intake key and service, A16) and ``ix_service_requests_pool_handling`` (the waiting line).

``pool_id`` has no database foreign key, like ``slots.capacity_pool_id`` (migration 0111).
Only an active non-legacy flow writes these columns, and the rollback gate keeps every such flow
shut (ADR 0122 decision 12); the previous image never reads them.

Downgrade refuses while an immediate request taken in on a pool exists (it would lose its pool
and its participant's access), then drops the indexes and the columns.

Revision ID: 0115
Revises: 0114
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0115"
down_revision: str | None = "0114"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUEST_COLUMNS = (
    "pool_id",
    "subject",
    "arrived_at",
    "public_ref",
    "access_hash",
    "intake_key_hash",
)


def upgrade() -> None:
    """Add the waiting limit, the request columns and their indexes."""
    op.add_column("capacity_pools", sa.Column("max_waiting", sa.Integer(), nullable=True))
    op.add_column("service_requests", sa.Column("pool_id", sa.Integer(), nullable=True))
    op.add_column("service_requests", sa.Column("subject", sa.String(length=64), nullable=True))
    op.add_column("service_requests", sa.Column("arrived_at", UTCDateTime(), nullable=True))
    op.add_column("service_requests", sa.Column("public_ref", sa.String(length=32), nullable=True))
    op.add_column("service_requests", sa.Column("access_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "service_requests", sa.Column("intake_key_hash", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_service_requests_pool_handling", "service_requests", ["pool_id", "handling"]
    )
    op.create_index(
        "uq_service_requests_public_ref", "service_requests", ["public_ref"], unique=True
    )
    op.create_index(
        "uq_service_requests_intake_key",
        "service_requests",
        ["group_id", "intake_key_hash"],
        unique=True,
        sqlite_where=sa.text("intake_key_hash IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop them again; refuse while an immediate request exists."""
    held = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM service_requests"
            " WHERE intake = 'on_demand' AND pool_id IS NOT NULL"
        )
    )
    if held.scalar_one():
        raise RuntimeError("immediate requests exist; they would lose their pool and access")
    op.drop_index("uq_service_requests_intake_key", table_name="service_requests")
    op.drop_index("uq_service_requests_public_ref", table_name="service_requests")
    op.drop_index("ix_service_requests_pool_handling", table_name="service_requests")
    with op.batch_alter_table("service_requests") as batch:
        for column in REQUEST_COLUMNS:
            batch.drop_column(column)
    with op.batch_alter_table("capacity_pools") as batch:
        batch.drop_column("max_waiting")
