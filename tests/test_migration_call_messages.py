"""Migration 0121 (ADR 0123 step 7b): the table of chat messages, expand only and reversible.

One new table, nothing existing touched: a booking written before it is read the same after it, the
rows of a chat go with their booking, and the downgrade drops the table and nothing else.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.adapters.db.models import Base, CallMessageRow
from app.domain.privacy import CHAT_FIELDS

NOW = "2026-01-01 00:00:00.000000"


@pytest.fixture
def before(alembic_config: Config, tmp_path: Path) -> Path:
    command.upgrade(alembic_config, "0120")
    return tmp_path / "migrate.db"


def _tables(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as database:
        return {
            name
            for (name,) in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }


def test_upgrade_adds_only_the_table_and_downgrade_removes_only_it(
    alembic_config: Config, before: Path
) -> None:
    old = _tables(before)

    command.upgrade(alembic_config, "0121")
    up = _tables(before)
    command.downgrade(alembic_config, "0120")

    assert up - old == {"call_messages"}
    assert old - up == set()
    assert _tables(before) == old


def test_the_table_has_the_columns_the_model_has_and_one_personal_column(
    alembic_config: Config, before: Path
) -> None:
    command.upgrade(alembic_config, "0121")
    engine = create_engine(f"sqlite:///{before}")
    migrated = {c["name"] for c in inspect(engine).get_columns("call_messages")}
    unique = inspect(engine).get_unique_constraints("call_messages")
    engine.dispose()

    assert migrated == {c.name for c in CallMessageRow.__table__.columns}
    assert migrated & set(CHAT_FIELDS) == set(CHAT_FIELDS)
    assert {tuple(u["column_names"]) for u in unique} == {("booking_id", "provider_message_id")}
    assert "call_messages" in Base.metadata.tables


def test_a_wrong_author_or_state_is_refused_by_the_database(
    alembic_config: Config, before: Path
) -> None:
    command.upgrade(alembic_config, "0121")
    insert = (
        "INSERT INTO call_messages (booking_id, author, message_text, at, state,"
        " provider_message_id) VALUES (1, ?, 'x', ?, ?, 'm')"
    )
    with closing(sqlite3.connect(before)) as database:
        database.execute("PRAGMA foreign_keys = OFF")
        for author, state in (("robot", "sent"), ("staff", "lost")):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(insert, (author, NOW, state))
        database.execute(insert, ("staff", NOW, "delivered"))
