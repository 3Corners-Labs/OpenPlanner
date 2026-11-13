"""Migration 0115 (ADR 0121 step 7): immediate intake and check-in on the same pool.

Upgrading keeps every request as it was (the new columns are empty), adds the waiting limit of a
pool and the indexes that keep one request per intake key and service (A16) and one request per
public reference; downgrading refuses while immediate work taken in on a pool exists and drops
the columns otherwise.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

AT = "2026-03-02 08:00:00.000000"
IMMEDIATE = (
    "INSERT INTO service_requests (id, organisation_id, group_id, flow_id, flow_revision,"
    " intake, handling, created_at, watchdog_at, pool_id, public_ref, intake_key_hash)"
    " VALUES (?, 1, 1, 1, 1, 'on_demand', 'queued', ?, ?, 1, ?, ?)"
)
PLAIN = (
    "INSERT INTO service_requests (id, organisation_id, group_id, flow_id, flow_revision,"
    " intake, handling, created_at, watchdog_at) VALUES (?, 1, 1, 1, 1, 'on_demand',"
    " 'queued', ?, ?)"
)


@pytest.fixture
def old_database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return a database at 0114 with a service, a flow and a request of step 5."""
    command.upgrade(alembic_config, "0114")
    path = tmp_path / "migrate.db"
    with closing(sqlite3.connect(path)) as database:
        database.execute(
            "INSERT INTO organisations (id, name, slug, idp_key) VALUES (1, 'Org', 'org', 'org')"
        )
        database.execute(
            "INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,"
            " min_notice_minutes, horizon_days, phone_mode, dispatch_kind, idp_key)"
            " VALUES (1, 1, 'Balie', 'balie', 'Europe/Amsterdam', 30, 60, 60, 'optional',"
            " 'contact_center', 'balie')"
        )
        database.execute(
            "INSERT INTO service_flows (id, group_id, code, state, current_revision)"
            " VALUES (1, 1, 'advies', 'active', 1)"
        )
        database.execute(
            "INSERT INTO flow_revisions (flow_id, revision, labels, intake_modes)"
            " VALUES (1, 1, '{}', 'on_demand')"
        )
        database.execute(PLAIN, (1, AT, AT))
        database.commit()
    return path


def test_upgrade_keeps_requests_and_guards_the_key_and_the_reference(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0115")

    with closing(sqlite3.connect(old_database)) as database:
        kept = database.execute(
            "SELECT pool_id, subject, arrived_at, public_ref, access_hash, intake_key_hash"
            " FROM service_requests WHERE id = 1"
        ).fetchone()
        assert kept == (None,) * 6
        database.execute("UPDATE capacity_pools SET max_waiting = 5")
        database.execute(IMMEDIATE, (2, AT, AT, "ref-a", "key-a"))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            database.execute(IMMEDIATE, (3, AT, AT, "ref-b", "key-a"))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            database.execute(IMMEDIATE, (3, AT, AT, "ref-a", "key-b"))
        database.execute(PLAIN, (4, AT, AT))  # many requests without a key or a reference


def test_downgrade_refuses_while_immediate_work_exists_and_drops_the_columns(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0115")
    with closing(sqlite3.connect(old_database)) as database:
        database.execute(IMMEDIATE, (2, AT, AT, "ref-a", "key-a"))
        database.commit()

    with pytest.raises(RuntimeError, match="immediate requests exist"):
        command.downgrade(alembic_config, "0114")

    with closing(sqlite3.connect(old_database)) as database:
        database.execute("DELETE FROM service_requests WHERE id = 2")
        database.commit()
    command.downgrade(alembic_config, "0114")
    with closing(sqlite3.connect(old_database)) as database:
        columns = {row[1] for row in database.execute("PRAGMA table_info(service_requests)")}
        pools = {row[1] for row in database.execute("PRAGMA table_info(capacity_pools)")}
        kept = database.execute("SELECT COUNT(*) FROM service_requests").fetchone()
    assert not {"pool_id", "public_ref", "intake_key_hash"} & columns
    assert "max_waiting" not in pools
    assert kept == (1,)
    command.upgrade(alembic_config, "head")
