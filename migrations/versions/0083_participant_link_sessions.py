"""Public booking references and participant link sessions (ADR 0093 steps 1, 2).

``bookings.public_ref`` names a booking in a URL (``/b/m/{public_ref}``) and authorises
nothing; every existing booking gets one. ``participant_link_sessions`` holds one row per
browser that opened a management link: only the ``sha256`` of the random cookie value, and
when the session lapses. ``participant_link_bookings`` lists the bookings such a browser holds
(at most 20). No personal data.

Revises 0082 (identity provider MFA policy, ADR 0100). Open PRs also claim numbers, so
recheck the head before merging.

Revision ID: 0083
Revises: 0082
Create Date: 2026-11-02 09:00:00.000000
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REF_BYTES = 16


def upgrade() -> None:
    """Add and fill ``public_ref``, then create the two session tables."""
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("public_ref", sa.String(length=32), nullable=True))
    connection = op.get_bind()
    ids = [row[0] for row in connection.execute(sa.text("SELECT id FROM bookings"))]
    for booking_id in ids:
        connection.execute(
            sa.text("UPDATE bookings SET public_ref = :ref WHERE id = :id"),
            {"ref": secrets.token_urlsafe(_REF_BYTES), "id": booking_id},
        )
    with op.batch_alter_table("bookings") as batch:
        batch.alter_column("public_ref", existing_type=sa.String(length=32), nullable=False)
        batch.create_unique_constraint(op.f("uq_bookings_public_ref"), ["public_ref"])
    op.create_table(
        "participant_link_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("value_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participant_link_sessions")),
        sa.UniqueConstraint("value_hash", name=op.f("uq_participant_link_sessions_value_hash")),
    )
    op.create_index(
        op.f("ix_participant_link_sessions_expires_at"),
        "participant_link_sessions",
        ["expires_at"],
    )
    op.create_table(
        "participant_link_bookings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["participant_link_sessions.id"],
            name=op.f("fk_participant_link_bookings_session_id_participant_link_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_participant_link_bookings_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participant_link_bookings")),
        sa.UniqueConstraint(
            "session_id", "booking_id", name=op.f("uq_participant_link_bookings_session_booking")
        ),
    )
    op.create_index(
        op.f("ix_participant_link_bookings_booking_id"),
        "participant_link_bookings",
        ["booking_id"],
    )


def downgrade() -> None:
    """Drop the session tables and ``public_ref``."""
    op.drop_index(
        op.f("ix_participant_link_bookings_booking_id"), table_name="participant_link_bookings"
    )
    op.drop_table("participant_link_bookings")
    op.drop_index(
        op.f("ix_participant_link_sessions_expires_at"), table_name="participant_link_sessions"
    )
    op.drop_table("participant_link_sessions")
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint(op.f("uq_bookings_public_ref"), type_="unique")
        batch.drop_column("public_ref")
