"""SQLite housekeeping (ADR 0040): incremental auto-vacuum and measuring the data volume."""

import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.engine import create_engine
from app.adapters.db.maintenance import (
    INCREMENTAL,
    enable_incremental_vacuum,
    measure_storage,
    reclaim_free_pages,
)


async def _pragma(engine: AsyncEngine, name: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text(f"PRAGMA {name}"))).scalar_one())


async def _fill_and_empty(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS filler (a TEXT)"))
        for _ in range(400):
            await conn.execute(text("INSERT INTO filler VALUES (:a)"), {"a": "x" * 1000})
        await conn.execute(text("DELETE FROM filler"))


def _size_with_wal(file: Path) -> int:
    return file.stat().st_size + file.with_name(f"{file.name}-wal").stat().st_size


async def test_a_migrated_database_is_in_incremental_mode(engine: AsyncEngine) -> None:
    assert await _pragma(engine, "auto_vacuum") == INCREMENTAL


async def test_an_existing_database_is_converted_once(tmp_path: Path) -> None:
    file = tmp_path / "old.db"
    with closing(sqlite3.connect(file)) as old:
        old.execute("PRAGMA journal_mode=WAL")
        old.execute("CREATE TABLE t (a)")
        old.commit()
    engine = create_engine(f"sqlite+aiosqlite:///{file}")
    try:
        async with engine.connect() as conn:
            first = await conn.run_sync(enable_incremental_vacuum)
            second = await conn.run_sync(enable_incremental_vacuum)
        mode = await _pragma(engine, "auto_vacuum")
    finally:
        await engine.dispose()

    assert (first, second, mode) == (True, False, INCREMENTAL)


async def test_freed_pages_go_back_to_the_file_system(engine: AsyncEngine) -> None:
    await _fill_and_empty(engine)
    assert await _pragma(engine, "freelist_count") > 0

    reclaimed = await reclaim_free_pages(engine)

    assert reclaimed > 0
    assert await _pragma(engine, "freelist_count") == 0


async def test_nothing_is_reclaimed_before_the_conversion(tmp_path: Path) -> None:
    file = tmp_path / "old.db"
    with closing(sqlite3.connect(file)) as old:
        old.execute("CREATE TABLE t (a)")
        old.commit()
    engine = create_engine(f"sqlite+aiosqlite:///{file}")
    try:
        assert await reclaim_free_pages(engine) == 0
    finally:
        await engine.dispose()


async def test_storage_counts_the_database_with_its_wal(
    engine: AsyncEngine, database_url: str
) -> None:
    await _fill_and_empty(engine)
    file = Path(database_url.removeprefix("sqlite+aiosqlite:///"))

    usage = measure_storage(database_url)

    assert usage is not None
    assert usage.database_bytes == _size_with_wal(file)
    assert 0 < usage.free_bytes <= usage.total_bytes


def test_storage_of_a_missing_or_foreign_database_is_unknown(tmp_path: Path) -> None:
    assert measure_storage(f"sqlite+aiosqlite:///{tmp_path / 'absent.db'}") is None
    assert measure_storage("postgresql://db/booking") is None
