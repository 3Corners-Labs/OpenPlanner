"""Migration 0118 (ADR 0124 point 8): the table of first sightings of webhook secrets.

Expand only: upgrading adds one empty table and touches nothing else; downgrading drops it.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config


def _tables(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as database:
        return {
            row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def _columns(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as database:
        return [row[1] for row in database.execute("PRAGMA table_info(webhook_secret_sightings)")]


def test_upgrading_adds_one_empty_table_and_downgrading_removes_it(
    alembic_config: Config, tmp_path: Path
) -> None:
    command.upgrade(alembic_config, "0117")
    path = tmp_path / "migrate.db"
    before = _tables(path)

    command.upgrade(alembic_config, "0118")

    assert _tables(path) - before == {"webhook_secret_sightings"}
    assert _columns(path) == ["platform", "fingerprint", "first_seen"]
    with closing(sqlite3.connect(path)) as database:
        assert database.execute("SELECT count(*) FROM webhook_secret_sightings").fetchone() == (0,)
    command.downgrade(alembic_config, "0117")
    assert _tables(path) == before
