"""Custom booking form fields and the split name (ADR 0055).

``group_form_fields`` holds a group's custom fields (no personal data); ``groups.form_version``
counts its changes. On ``bookings`` the single ``name`` becomes ``first_name`` and
``last_name`` (first word, the rest; names were stored with single spaces), and
``form_answers`` holds the answers as JSON. All three are personal data and purged with the
other contact columns.

Revises 0044 (team invite address, ADR 0043 step 3), which was merged first; it revises the
merge revision 0052.

The downgrade joins the two name parts again and drops the answers and the fields: answers
to custom fields cannot be kept in the older schema.

Revision ID: 0053
Revises: 0044
Create Date: 2026-10-29 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``group_form_fields``, add the form version, split the name, add the answers."""
    op.create_table(
        "group_form_fields",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("classification", sa.String(length=8), nullable=False),
        sa.Column("options", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("minimum", sa.Integer(), nullable=True),
        sa.Column("maximum", sa.Integer(), nullable=True),
        sa.Column("labels", sa.Text(), nullable=False),
        sa.Column("hints", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("option_labels", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("retired", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "classification IN ('pii', 'not_pii')",
            name=op.f("ck_group_form_fields_classification"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_group_form_fields_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_form_fields")),
        sa.UniqueConstraint("group_id", "key", name=op.f("uq_group_form_fields_group_id_key")),
    )
    with op.batch_alter_table("groups") as batch:
        batch.add_column(
            sa.Column("form_version", sa.Integer(), server_default=sa.text("1"), nullable=False)
        )
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("first_name", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("last_name", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("form_answers", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE bookings SET
            first_name = CASE WHEN instr(name, ' ') > 0
                THEN substr(name, 1, instr(name, ' ') - 1) ELSE name END,
            last_name = CASE WHEN instr(name, ' ') > 0
                THEN substr(name, instr(name, ' ') + 1) ELSE '' END
        WHERE name IS NOT NULL
        """
    )
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("name")


def downgrade() -> None:
    """Join the name again; drop the answers, the form version and the fields."""
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("name", sa.String(length=100), nullable=True))
    op.execute(
        """
        UPDATE bookings SET name = trim(first_name || ' ' || coalesce(last_name, ''))
        WHERE first_name IS NOT NULL
        """
    )
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("form_answers")
        batch.drop_column("last_name")
        batch.drop_column("first_name")
    with op.batch_alter_table("groups") as batch:
        batch.drop_column("form_version")
    op.drop_table("group_form_fields")
