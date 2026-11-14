"""Migration 0116 (ADR 0123 step 7a): the kind of contact-center task.

Upgrading keeps every routing and task as a form (the routing by its column default, the task as
NULL); downgrading drops both columns and refuses while a conversation exists, so a conversation
task is never read as a work item.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROUTING = (
    "INSERT INTO group_contact_center (group_id, kind, routing_value, attributes)"
    " VALUES (1, 'webex_cc', 'team', '{}')"
)


@pytest.fixture
def old_database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return a database at 0115 with a group that has a routing."""
    command.upgrade(alembic_config, "0115")
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
        database.execute(ROUTING)
        database.commit()
    return path


def _columns(path: Path, table: str) -> list[str]:
    with closing(sqlite3.connect(path)) as database:
        return [row[1] for row in database.execute(f"PRAGMA table_info({table})")]  # noqa: S608


def test_an_existing_routing_is_a_form_and_nothing_else_changes(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0116")

    with closing(sqlite3.connect(old_database)) as database:
        assert database.execute(
            "SELECT kind, routing_value, task_kind FROM group_contact_center"
        ).fetchall() == [("webex_cc", "team", "form")]
    assert {"contact_center_task_kind", "contact_center_attempt_kind"} <= set(
        _columns(old_database, "bookings")
    )


def test_downgrade_refuses_while_a_conversation_routing_exists(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0116")
    with closing(sqlite3.connect(old_database)) as database:
        database.execute("UPDATE group_contact_center SET task_kind = 'conversation'")
        database.commit()

    with pytest.raises(RuntimeError, match="conversation routings or tasks exist"):
        command.downgrade(alembic_config, "0115")

    with closing(sqlite3.connect(old_database)) as database:
        database.execute("UPDATE group_contact_center SET task_kind = 'form'")
        database.commit()
    command.downgrade(alembic_config, "0115")
    assert "task_kind" not in _columns(old_database, "group_contact_center")
    assert "contact_center_task_kind" not in _columns(old_database, "bookings")
    assert "contact_center_attempt_kind" not in _columns(old_database, "bookings")
