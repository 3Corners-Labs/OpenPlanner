"""SQLite backups for the host updater (ADR 0028): consistent copy, private, pruned, restorable."""

import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.adapters.db.backup import (
    BACKUP_LIFETIME,
    backup_database,
    database_path,
    prune_backups,
    restore_database,
)

NOW = datetime(2026, 10, 26, 22, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "booking.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE t (v TEXT)")
        db.execute("INSERT INTO t VALUES ('before')")
    return path


def rows(path: Path) -> list[str]:
    with sqlite3.connect(path) as db:
        return [v for (v,) in db.execute("SELECT v FROM t")]


def test_database_path_from_the_url() -> None:
    assert database_path("sqlite+aiosqlite:///data/booking.db") == Path("data/booking.db")
    assert database_path("sqlite+aiosqlite:////srv/data/booking.db") == Path("/srv/data/booking.db")
    with pytest.raises(ValueError, match="SQLite"):
        database_path("postgresql+asyncpg://db/booking")


def test_backup_is_a_private_consistent_copy(database: Path, tmp_path: Path) -> None:
    target = backup_database(database, tmp_path / "backups", "request-7")
    assert target == tmp_path / "backups" / "request-7.db"
    assert rows(target) == ["before"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("name", ["../escape", "a/b", "", "x" * 65, "naam met spatie"])
def test_backup_name_is_a_plain_word(database: Path, tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        backup_database(database, tmp_path / "backups", name)


def test_restore_puts_the_backup_back(database: Path, tmp_path: Path) -> None:
    backup_database(database, tmp_path / "backups", "request-7")
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO t VALUES ('after')")
    restore_database(database, tmp_path / "backups", "request-7")
    assert rows(database) == ["before"]


def test_restore_of_a_missing_backup_fails(database: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        restore_database(database, tmp_path / "backups", "request-8")


def test_prune_deletes_backups_older_than_a_day(database: Path, tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    old = backup_database(database, directory, "old")
    new = backup_database(database, directory, "new")
    stale = (NOW - BACKUP_LIFETIME - timedelta(minutes=1)).timestamp()
    os.utime(old, (stale, stale))
    os.utime(new, (NOW.timestamp(), NOW.timestamp()))
    assert prune_backups(directory, NOW) == 1
    assert not old.exists()
    assert new.exists()
    assert prune_backups(tmp_path / "none", NOW) == 0
