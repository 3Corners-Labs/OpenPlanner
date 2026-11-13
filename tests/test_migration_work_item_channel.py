"""Migration 0114 (ADR 0121 step 6b): the channel of a work item and the attempt states.

Upgrading keeps every work item, now of the own queue; the database still keeps one active work
item per request, now counting a contact-center attempt as active, so the own queue and the
contact center can never both hold a request; downgrading refuses while an attempt exists and
restores the old states otherwise.
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
ATTEMPT = (
    "INSERT INTO work_items (request_id, state, assigned_at, channel) "
    "VALUES (?, ?, ?, 'contact_center')"
)


@pytest.fixture
def old_database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return a database at 0113 with a request and an own-queue work item that ended."""
    command.upgrade(alembic_config, "0113")
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
        database.execute(REQUEST, (1, AT, AT))
        database.execute(ITEM, (1, "released", AT))
        database.commit()
    return path


def test_existing_work_stays_the_own_queues_and_an_attempt_counts_as_active(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0114")

    with closing(sqlite3.connect(old_database)) as database:
        assert database.execute("SELECT channel, state FROM work_items").fetchall() == [
            ("own_queue", "released")
        ]
        database.execute(ATTEMPT, (1, "reconciliation_required", AT))
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(ITEM, (1, "assigned", AT))  # the fence: one active per request
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(
                "INSERT INTO work_items (request_id, state, assigned_at, channel)"
                " VALUES (1, 'released', ?, 'elsewhere')",
                (AT,),
            )


def test_downgrade_refuses_while_an_attempt_exists_and_restores_the_states(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0114")
    with closing(sqlite3.connect(old_database)) as database:
        database.execute(ATTEMPT, (1, "fenced", AT))
        database.commit()

    with pytest.raises(RuntimeError, match="contact-center attempts exist"):
        command.downgrade(alembic_config, "0113")

    with closing(sqlite3.connect(old_database)) as database:
        database.execute("DELETE FROM work_items WHERE channel = 'contact_center'")
        database.commit()
    command.downgrade(alembic_config, "0113")
    with closing(sqlite3.connect(old_database)) as database:
        columns = [row[1] for row in database.execute("PRAGMA table_info(work_items)")]
        assert "channel" not in columns
        database.execute(ITEM, (1, "assigned", AT))
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(ITEM, (1, "in_progress", AT))
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(ITEM, (1, "dispatched", AT))
