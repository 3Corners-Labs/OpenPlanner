"""Staffing of a counted capacity pool and the pool on a slot (ADR 0122 step 2).

Expand only (ADR 0116): one nullable column ``capacity_pools.timezone``, two new tables
(``pool_staffing_rules``, ``pool_staffing_overrides``), two nullable columns on ``slots``
(``capacity_pool_id``, ``occupied_until``) and their indexes. Nothing is dropped, renamed or
tightened, and nothing is backfilled: a legacy pool gets no staffing, a counted pool starts empty,
and every existing slot is a legacy slot (both new columns NULL).

The legacy unique index ``uq_slots_group_start_scheduled`` is recreated with
``AND capacity_pool_id IS NULL``. While every slot has a NULL pool it is the same index; for the
previous image, which only writes NULL pools, it stays exactly as strict. ``uq_slots_pool`` lets
the bookings of one service, pool, start and length share a slot row, as legacy bookings share
one, and ``ix_slots_pool_start`` serves the load query of a pool.

The two ``slots`` columns are added with a plain ``ALTER TABLE ADD COLUMN`` (no table rebuild).
``capacity_pool_id`` has no database foreign key, like ``bookings.flow_id`` (migration 0109):
SQLite cannot add a named constraint to an existing table without rebuilding ``slots``, which is
referenced by ``bookings``; a pool is archived, never deleted, and a pool slot is only written by
the booking path of ADR 0122 step 5, which reads the pool in the same transaction.

``uq_slots_pool`` has a nullable column (``occupied_until``), and SQLite treats NULLs as distinct
in a unique index: the booking path of ADR 0122 step 5 must always write both ``capacity_pool_id``
and ``occupied_until`` of a pool slot, or the index guards nothing for it.

The exceptions table has a stricter check than a service's: a window is all of opens, closes and
units, or none of them (a closed day).

Downgrade drops what was added and restores the old index, and refuses while a slot carries a
pool: the bookings on it would lose their pool, and the restored unique index could then fail
(the precedent is migration 0010 for direct-call slots). It drops the staffing tables without
refusing: they hold configuration only, which is lost (a package or the screens can restore it).

Revision ID: 0111
Revises: 0110
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0111"
down_revision: str | None = "0110"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OFFERS = "('scheduled', 'direct', 'both')"
"""The stored values of a block's offer (ADR 0072), as ``ScheduleRuleRow`` has them."""


def _pool_fk(table: str) -> sa.ForeignKeyConstraint:
    """Return the foreign key of a staffing row to its pool, named like the convention does."""
    return sa.ForeignKeyConstraint(
        ["pool_id"],
        ["capacity_pools.id"],
        name=op.f(f"fk_{table}_pool_id_capacity_pools"),
        ondelete="CASCADE",
    )


def _create_staffing_tables() -> None:
    """Create the two staffing tables; a pool's rows go with the pool."""
    op.create_table(
        "pool_staffing_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pool_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("opens", sa.Time(), nullable=False),
        sa.Column("closes", sa.Time(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=True),
        sa.Column(
            "offer", sa.String(length=9), server_default=sa.text("'scheduled'"), nullable=False
        ),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=op.f("ck_pool_staffing_rules_weekday")),
        sa.CheckConstraint("opens < closes", name=op.f("ck_pool_staffing_rules_window")),
        sa.CheckConstraint("units >= 0", name=op.f("ck_pool_staffing_rules_units")),
        sa.CheckConstraint(f"offer IN {OFFERS}", name=op.f("ck_pool_staffing_rules_offer")),
        _pool_fk("pool_staffing_rules"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pool_staffing_rules")),
    )
    op.create_index("ix_pool_staffing_rules_pool_id", "pool_staffing_rules", ["pool_id"])
    op.create_table(
        "pool_staffing_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pool_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("opens", sa.Time(), nullable=True),
        sa.Column("closes", sa.Time(), nullable=True),
        sa.Column("units", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=50), nullable=True),
        sa.Column(
            "offer", sa.String(length=9), server_default=sa.text("'scheduled'"), nullable=False
        ),
        sa.CheckConstraint(
            "(opens IS NULL) = (closes IS NULL) AND (opens IS NULL) = (units IS NULL) "
            "AND (opens IS NULL OR (opens < closes AND units >= 0))",
            name=op.f("ck_pool_staffing_overrides_window_or_closed"),
        ),
        sa.CheckConstraint(f"offer IN {OFFERS}", name=op.f("ck_pool_staffing_overrides_offer")),
        _pool_fk("pool_staffing_overrides"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pool_staffing_overrides")),
    )
    op.create_index(
        "ix_pool_staffing_overrides_pool_id_day", "pool_staffing_overrides", ["pool_id", "day"]
    )


def upgrade() -> None:
    """Add the zone, the staffing tables, the two slot columns and the indexes."""
    op.add_column("capacity_pools", sa.Column("timezone", sa.String(length=64), nullable=True))
    _create_staffing_tables()
    op.add_column("slots", sa.Column("capacity_pool_id", sa.Integer(), nullable=True))
    op.add_column("slots", sa.Column("occupied_until", UTCDateTime(), nullable=True))
    op.drop_index("uq_slots_group_start_scheduled", table_name="slots")
    op.create_index(
        "uq_slots_group_start_scheduled",
        "slots",
        ["group_id", "start"],
        unique=True,
        sqlite_where=sa.text("kind = 'scheduled' AND capacity_pool_id IS NULL"),
    )
    op.create_index(
        "uq_slots_pool",
        "slots",
        ["capacity_pool_id", "group_id", "start", "occupied_until"],
        unique=True,
        sqlite_where=sa.text("capacity_pool_id IS NOT NULL"),
    )
    op.create_index("ix_slots_pool_start", "slots", ["capacity_pool_id", "start"])


def downgrade() -> None:
    """Drop what was added and restore the old index; refuse while a slot carries a pool."""
    pooled = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM slots WHERE capacity_pool_id IS NOT NULL")
    )
    if pooled.scalar_one():
        raise RuntimeError("pool slots exist; downgrading would lose the pool of their bookings")
    op.drop_index("ix_slots_pool_start", table_name="slots")
    op.drop_index("uq_slots_pool", table_name="slots")
    op.drop_index("uq_slots_group_start_scheduled", table_name="slots")
    with op.batch_alter_table("slots") as batch:
        batch.drop_column("occupied_until")
        batch.drop_column("capacity_pool_id")
    op.create_index(
        "uq_slots_group_start_scheduled",
        "slots",
        ["group_id", "start"],
        unique=True,
        sqlite_where=sa.text("kind = 'scheduled'"),
    )
    op.drop_index("ix_pool_staffing_overrides_pool_id_day", table_name="pool_staffing_overrides")
    op.drop_table("pool_staffing_overrides")
    op.drop_index("ix_pool_staffing_rules_pool_id", table_name="pool_staffing_rules")
    op.drop_table("pool_staffing_rules")
    with op.batch_alter_table("capacity_pools") as batch:
        batch.drop_column("timezone")
