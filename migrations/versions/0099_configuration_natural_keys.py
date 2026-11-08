"""Natural keys for closure scenarios and alert targets (ADR 0099 point 2).

A configuration document addresses objects by natural keys, never by ids. A closure scenario
is known by its name within its organisation or group, an alert target by its kind and address
within the platform or its organisation; neither had a unique constraint. The upgrade first
makes existing duplicates distinct (a scenario gets `` (2)``, `` (3)`` after its name; a
duplicate alert target is deleted, as it only repeated the address), then creates the unique
indexes: partial ones, because a NULL owner does not make a key unique in SQLite. The
downgrade drops the indexes; the names stay as they were made.

Revises 0096. Open PRs also claim numbers, so recheck the head before merging.

Revision ID: 0099
Revises: 0096
Create Date: 2026-11-08 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0099"
down_revision: str | None = "0096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME_LENGTH = 80


def upgrade() -> None:
    """Make duplicates distinct, then create the four unique indexes."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, organisation_id, group_id, name FROM closure_scenarios ORDER BY id")
    ).fetchall()
    taken = {(row.organisation_id, row.group_id, row.name) for row in rows}
    seen: set[tuple[int, int | None, str]] = set()
    for row in rows:
        key = (row.organisation_id, row.group_id, row.name)
        if key not in seen:
            seen.add(key)
            continue
        number = 2
        while True:
            suffix = f" ({number})"
            name = row.name[: NAME_LENGTH - len(suffix)] + suffix
            candidate = (row.organisation_id, row.group_id, name)
            if candidate not in taken:
                break
            number += 1
        taken.add(candidate)
        seen.add(candidate)
        bind.execute(
            sa.text("UPDATE closure_scenarios SET name = :name WHERE id = :id"),
            {"name": name, "id": row.id},
        )
    # Foreign keys are not enforced while Alembic runs, so the deliveries of a target that is
    # deleted here are deleted with it, as ON DELETE CASCADE does in the application.
    repeated = (
        "SELECT id FROM alert_targets WHERE id NOT IN ("
        "SELECT MIN(id) FROM alert_targets GROUP BY COALESCE(organisation_id, 0), kind, address)"
    )
    bind.execute(sa.text(f"DELETE FROM alert_deliveries WHERE target_id IN ({repeated})"))  # noqa: S608  # nosec B608 - fixed text
    bind.execute(sa.text(f"DELETE FROM alert_targets WHERE id IN ({repeated})"))  # noqa: S608  # nosec B608 - fixed text
    op.create_index(
        "uq_closure_scenarios_organisation_name",
        "closure_scenarios",
        ["organisation_id", "name"],
        unique=True,
        sqlite_where=sa.text("group_id IS NULL"),
    )
    op.create_index(
        "uq_closure_scenarios_group_name",
        "closure_scenarios",
        ["group_id", "name"],
        unique=True,
        sqlite_where=sa.text("group_id IS NOT NULL"),
    )
    op.create_index(
        "uq_alert_targets_organisation_address",
        "alert_targets",
        ["organisation_id", "kind", "address"],
        unique=True,
        sqlite_where=sa.text("organisation_id IS NOT NULL"),
    )
    op.create_index(
        "uq_alert_targets_platform_address",
        "alert_targets",
        ["kind", "address"],
        unique=True,
        sqlite_where=sa.text("organisation_id IS NULL"),
    )


def downgrade() -> None:
    """Drop the four unique indexes."""
    op.drop_index("uq_alert_targets_platform_address", table_name="alert_targets")
    op.drop_index("uq_alert_targets_organisation_address", table_name="alert_targets")
    op.drop_index("uq_closure_scenarios_group_name", table_name="closure_scenarios")
    op.drop_index("uq_closure_scenarios_organisation_name", table_name="closure_scenarios")
