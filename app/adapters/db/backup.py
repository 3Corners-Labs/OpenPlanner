"""SQLite backups for the host updater (ADR 0028).

The updater backs up the database before it changes anything, and puts the backup back when
the new version does not become healthy. Backups hold personal data: they are written with
mode 600 next to the database (``data/backups``), never leave the data volume, and are deleted
after ``BACKUP_LIFETIME``. Both directions use SQLite's online backup API, so a copy is
consistent even while the database is in WAL mode.
"""

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

BACKUP_LIFETIME = timedelta(hours=24)
"""How long a backup is kept: the restore window of an update (``RESTORE_WINDOW``)."""

_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SUFFIX = ".db"


def database_path(database_url: str) -> Path:
    """Return the file of a SQLite ``DATABASE_URL``.

    Raises:
        ValueError: For any other database.
    """
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise ValueError("backups need a SQLite database file")
    return Path(url.database)


def _backup_file(directory: Path, name: str) -> Path:
    """Return the path of backup ``name``; the name is a plain word, never a path."""
    if not _NAME.fullmatch(name):
        raise ValueError("backup name must be letters, digits, '-' or '_'")
    return directory / f"{name}{_SUFFIX}"


def _copy(source: Path, target: Path) -> None:
    """Copy one SQLite database into another with the online backup API."""
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def backup_database(database: Path, directory: Path, name: str) -> Path:
    """Write a consistent copy of ``database`` as ``directory/name.db`` (mode 600).

    Raises:
        ValueError: If ``name`` is not a plain word.
    """
    target = _backup_file(directory, name)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    target.touch(mode=0o600)
    _copy(database, target)
    target.chmod(0o600)
    return target


def restore_database(database: Path, directory: Path, name: str) -> None:
    """Put backup ``name`` back into ``database``; the application must be stopped.

    Raises:
        ValueError: If ``name`` is not a plain word.
        FileNotFoundError: If there is no such backup.
    """
    source = _backup_file(directory, name)
    if not source.is_file():
        raise FileNotFoundError(source.name)
    _copy(source, database)


def prune_backups(directory: Path, now: datetime) -> int:
    """Delete backups older than ``BACKUP_LIFETIME``; return how many were deleted."""
    if not directory.is_dir():
        return 0
    cutoff = (now - BACKUP_LIFETIME).timestamp()
    deleted = 0
    for backup in directory.glob(f"*{_SUFFIX}"):
        if backup.stat().st_mtime < cutoff:
            backup.unlink()
            deleted += 1
    return deleted
