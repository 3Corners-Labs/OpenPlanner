"""A stable code for a counted capacity pool (ADR 0121 step 2f).

Expand only (ADR 0116): one nullable column ``capacity_pools.code`` and a partial unique index on
``(organisation_id, code)``; the previous image neither reads nor writes either, and no existing
column changes meaning. A pool was identified by its name, which an admin can change; the
configuration document identifies it by this code, exactly like a skill.

Backfill, for every COUNTED pool in id order: the code is the name lower-cased, every run of
characters outside ``a-z0-9`` becoming one hyphen, trimmed of hyphens, cut to 40 characters and
trimmed again, ``pool`` when nothing is left; a code an organisation already has gets ``-2``,
``-3``, ... (the base is cut so that the whole stays within 40 characters). A legacy pool keeps
NULL: it is identified by its service. The rule is the one of
``app.domain.capacity.derive_pool_code``; it is written out here because a migration imports no
application code, and a test compares the two.

Revision ID: 0110
Revises: 0109
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0110"
down_revision: str | None = "0109"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE_LENGTH = 40
"""The longest code, the grammar of a skill code."""


def _base(name: str) -> str:
    """Return the code a name gives, before it is made unique."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:CODE_LENGTH].strip("-")
    return cleaned or "pool"


def _backfill() -> None:
    """Give every counted pool a code, unique within its organisation."""
    bind = op.get_bind()
    pools = sa.table(
        "capacity_pools",
        sa.column("id"),
        sa.column("organisation_id"),
        sa.column("name"),
        sa.column("mode"),
        sa.column("code"),
    )
    taken: dict[int, set[str]] = {}
    for pool in bind.execute(
        sa.select(pools.c.id, pools.c.organisation_id, pools.c.name)
        .where(pools.c.mode == "counted")
        .order_by(pools.c.id)
    ).all():
        used = taken.setdefault(pool.organisation_id, set())
        base = _base(pool.name)
        code, number = base, 1
        while code in used:
            number += 1
            suffix = f"-{number}"
            code = base[: CODE_LENGTH - len(suffix)].rstrip("-") + suffix
        used.add(code)
        bind.execute(sa.update(pools).where(pools.c.id == pool.id).values(code=code))


def upgrade() -> None:
    """Add the column and the index, then backfill the counted pools."""
    op.add_column("capacity_pools", sa.Column("code", sa.String(length=40), nullable=True))
    op.create_index(
        "uq_capacity_pools_organisation_id_code",
        "capacity_pools",
        ["organisation_id", "code"],
        unique=True,
        sqlite_where=sa.text("code IS NOT NULL"),
    )
    _backfill()


def downgrade() -> None:
    """Drop the index and the column."""
    op.drop_index("uq_capacity_pools_organisation_id_code", table_name="capacity_pools")
    with op.batch_alter_table("capacity_pools") as batch:
        batch.drop_column("code")
