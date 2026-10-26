"""Emergency closures: closures, their notices and suspended bookings (ADR 0023).

Datetime columns hold naive UTC, like every other table.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-25 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "'confirmed', 'cancelled', 'completed', 'no_show'"


def upgrade() -> None:
    op.create_table(
        "closures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_closures_group_id_groups"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_closures")),
    )
    op.create_index(op.f("ix_closures_group_id"), "closures", ["group_id"], unique=False)
    op.create_table(
        "closure_notices",
        sa.Column("closure_id", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("text", sa.String(length=1000), nullable=False),
        sa.ForeignKeyConstraint(
            ["closure_id"],
            ["closures.id"],
            name=op.f("fk_closure_notices_closure_id_closures"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("closure_id", "locale", name=op.f("pk_closure_notices")),
    )
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("closure_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_bookings_closure_id_closures"),
            "closures",
            ["closure_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.drop_constraint(op.f("ck_bookings_status"), type_="check")
        batch.create_check_constraint(
            op.f("ck_bookings_status"), f"status IN ({_STATUSES}, 'suspended')"
        )


def downgrade() -> None:
    # Suspended bookings cannot exist without closures: they become cancelled now, with a purge
    # job due at once, so no personal data outlives the closure it was kept for.
    op.execute(
        "INSERT INTO jobs (booking_id, type, run_at, status, attempts) "
        "SELECT id, 'purge_pii', CURRENT_TIMESTAMP, 'pending', 0 FROM bookings "
        "WHERE status = 'suspended'"
    )
    op.execute(
        "UPDATE bookings SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP "
        "WHERE status = 'suspended'"
    )
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint(op.f("ck_bookings_status"), type_="check")
        batch.create_check_constraint(op.f("ck_bookings_status"), f"status IN ({_STATUSES})")
        batch.drop_constraint(op.f("fk_bookings_closure_id_closures"), type_="foreignkey")
        batch.drop_column("closure_id")
    op.drop_table("closure_notices")
    op.drop_index(op.f("ix_closures_group_id"), table_name="closures")
    op.drop_table("closures")
