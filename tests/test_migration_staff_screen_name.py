"""Migration 0118 (ADR 0123 step 5c): the screen name of a staff member.

An expand step (ADR 0116): one nullable column on ``staff``, no row written. Upgrading leaves every
staff record without a name, so the participant sees the organisation's word alone until someone
sets one; downgrading drops the column and the names with it, which the previous image never had.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

STAFF = (
    "INSERT INTO staff (id, subject, display_name, roles, first_seen_at, last_login_at,"
    " last_seen_at, status) VALUES (1, 'sub-1', 'Sanne de Vries', 'org_admin',"
    " '2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'active')"
)


@pytest.fixture
def old_database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return a database at 0117 with one staff record."""
    command.upgrade(alembic_config, "0117")
    path = tmp_path / "migrate.db"
    with closing(sqlite3.connect(path)) as database:
        database.execute(STAFF)
        database.commit()
    return path


def _columns(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as database:
        return [row[1] for row in database.execute("PRAGMA table_info(staff)")]


def test_an_existing_staff_record_has_no_screen_name_and_nothing_else_changes(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0118")

    with closing(sqlite3.connect(old_database)) as database:
        assert database.execute(
            "SELECT subject, display_name, roles, screen_name FROM staff"
        ).fetchall() == [("sub-1", "Sanne de Vries", "org_admin", None)]
    assert "screen_name" in _columns(old_database)


def test_the_downgrade_drops_the_column_and_keeps_the_rest(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0118")
    with closing(sqlite3.connect(old_database)) as database:
        database.execute("UPDATE staff SET screen_name = 'Sanne'")
        database.commit()

    command.downgrade(alembic_config, "0117")

    assert "screen_name" not in _columns(old_database)
    with closing(sqlite3.connect(old_database)) as database:
        assert database.execute("SELECT subject, display_name FROM staff").fetchall() == [
            ("sub-1", "Sanne de Vries")
        ]
    command.upgrade(alembic_config, "0118")  # and up again
    assert "screen_name" in _columns(old_database)
