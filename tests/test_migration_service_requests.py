"""Migration 0113 (ADR 0121 step 5): requests, work items and the request record, expand only.

Upgrading adds three tables and touches no row; the database itself keeps one active work item
per request and one request per booking; downgrading drops the tables and refuses while a
request-level record holds personal data, so data never disappears outside the retention rules.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

AT = "2026-03-02 08:00:00.000000"
REQUEST = (
    "INSERT INTO service_requests (id, organisation_id, group_id, flow_id, flow_revision,"
    " intake, handling, created_at, watchdog_at) VALUES (?, 1, 1, 1, 1, 'on_demand', 'queued',"
    " ?, ?)"
)
ITEM = "INSERT INTO work_items (request_id, state, assigned_at) VALUES (?, ?, ?)"


@pytest.fixture
def old_database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return a database at 0111 with an organisation, a group and a published flow."""
    command.upgrade(alembic_config, "0111")
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
        database.commit()
    return path


def tables(path: Path) -> set[str]:
    """Return the names of the tables in the database."""
    with closing(sqlite3.connect(path)) as database:
        return {r[0] for r in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_upgrade_adds_the_three_tables_and_the_database_keeps_one_active_item(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")

    assert {"service_requests", "work_items", "request_personal_data"} <= tables(old_database)
    with closing(sqlite3.connect(old_database)) as database:
        database.execute("PRAGMA foreign_keys = ON")
        database.execute(REQUEST, (1, AT, AT))
        database.execute(ITEM, (1, "released", AT))
        database.execute(ITEM, (1, "assigned", AT))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            database.execute(ITEM, (1, "in_progress", AT))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            database.execute(
                "INSERT INTO service_requests (organisation_id, group_id, flow_id,"
                " flow_revision, intake, handling, created_at, watchdog_at)"
                " VALUES (1, 1, 1, 1, 'appointment', 'queued', ?, ?)",
                (AT, AT),
            )
        database.execute("INSERT INTO request_personal_data (request_id) VALUES (1)")
        database.execute("DELETE FROM service_requests WHERE id = 1")
        assert database.execute("SELECT COUNT(*) FROM work_items").fetchone() == (0,)
        assert database.execute("SELECT COUNT(*) FROM request_personal_data").fetchone() == (0,)


def test_downgrade_drops_the_tables_and_upgrading_again_works(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")
    with closing(sqlite3.connect(old_database)) as database:
        database.execute(REQUEST, (1, AT, AT))
        database.execute(ITEM, (1, "assigned", AT))
        database.commit()

    command.downgrade(alembic_config, "0111")

    assert not {"service_requests", "work_items", "request_personal_data"} & tables(old_database)
    command.upgrade(alembic_config, "head")
    assert "service_requests" in tables(old_database)


def test_downgrade_refuses_while_a_record_holds_personal_data(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")
    with closing(sqlite3.connect(old_database)) as database:
        database.execute(REQUEST, (1, AT, AT))
        database.execute(
            "INSERT INTO request_personal_data (request_id, first_name) VALUES (1, 'Ada')"
        )
        database.commit()

    with pytest.raises(RuntimeError, match="request-level personal data exists"):
        command.downgrade(alembic_config, "0111")

    assert "request_personal_data" in tables(old_database)
