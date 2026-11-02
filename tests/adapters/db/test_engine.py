from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.adapters.db.engine import create_engine, ping


async def test_sqlite_runs_in_wal_mode_with_foreign_keys(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'wal.db'}")
    try:
        async with engine.connect() as conn:
            journal = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            foreign_keys = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
            busy = (await conn.execute(text("PRAGMA busy_timeout"))).scalar_one()
    finally:
        await engine.dispose()

    assert journal == "wal"
    assert foreign_keys == 1
    assert busy >= 5000


async def test_creates_parent_directory_for_sqlite_file(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "dir" / "booking.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_file}")
    try:
        await ping(engine)
    finally:
        await engine.dispose()

    assert db_file.exists()


async def test_a_failing_statement_shows_no_bound_values(tmp_path: Path) -> None:
    """ASVS V16.2.5: the exception text names the statement, never the values bound to it."""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'hidden.db'}")
    secret = "ada@example.org"
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE people (email TEXT NOT NULL UNIQUE, n TEXT)"))
            await conn.execute(text("INSERT INTO people VALUES (:e, 'x')"), {"e": secret})
        with pytest.raises(IntegrityError) as duplicate:
            async with engine.begin() as conn:
                await conn.execute(text("INSERT INTO people VALUES (:e, 'y')"), {"e": secret})
        with pytest.raises(OperationalError) as missing:
            async with engine.begin() as conn:
                await conn.execute(text("INSERT INTO nowhere VALUES (:e)"), {"e": secret})
    finally:
        await engine.dispose()

    for raised in (duplicate, missing):
        assert secret not in str(raised.value)
        assert "[parameters:" not in str(raised.value)
        assert "INSERT INTO" in str(raised.value)  # still tells which statement failed
