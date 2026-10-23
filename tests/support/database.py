"""Migrated SQLite databases for integration tests."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]


def migrated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "test") -> str:
    """Create a SQLite file in ``tmp_path``, migrate it to head and return its URL."""
    url = f"sqlite+aiosqlite:///{tmp_path / f'{name}.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    return url
