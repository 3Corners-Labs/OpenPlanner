from pathlib import Path

from sqlalchemy import text

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
