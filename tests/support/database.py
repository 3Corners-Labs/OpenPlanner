"""Migrated SQLite databases for integration tests.

Migrating from scratch costs more with every migration, and most integration tests want a
fresh database at head. The first call per test process migrates a template once; every call
copies it, after a WAL checkpoint, so each test still gets its own file.
"""

import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]

_template: Path | None = None


def _migrated_template(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a database file at head, migrated once per test process."""
    global _template
    if _template is None:
        path = Path(tempfile.mkdtemp(prefix="openplanner-template-")) / "template.db"
        with monkeypatch.context() as scoped:
            scoped.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
            scoped.setenv("ENVIRONMENT", "test")
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "migrations"))
            command.upgrade(config, "head")
        with closing(sqlite3.connect(path)) as database:
            database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        _template = path
    return _template


def migrated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "test") -> str:
    """Create a SQLite file at head in ``tmp_path`` and return its URL."""
    target = tmp_path / f"{name}.db"
    shutil.copyfile(_migrated_template(monkeypatch), target)
    url = f"sqlite+aiosqlite:///{target}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    return url
