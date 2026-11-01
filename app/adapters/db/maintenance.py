"""Housekeeping of the SQLite file: give freed pages back and measure the data volume.

SQLite keeps the pages of deleted rows in the file unless the database is in incremental
auto-vacuum mode (ADR 0040). New databases get that mode from the engine's connect pragmas;
``enable_incremental_vacuum`` converts an existing one once, with a ``VACUUM``, and
``reclaim_free_pages`` then returns freed pages to the file system whenever the worker asks.

These pragmas cannot run inside a transaction, so they go through the driver connection.
"""

import shutil
from pathlib import Path

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.backup import database_path
from app.config import Settings
from app.domain.storage import MIB, StorageLimits, StorageUsage

INCREMENTAL = 2
"""Value of ``PRAGMA auto_vacuum`` in incremental mode."""


def enable_incremental_vacuum(connection: Connection) -> bool:
    """Switch an existing SQLite database to incremental auto-vacuum; return whether it did.

    A no-op when the database already is in that mode. Otherwise runs ``VACUUM``, which locks
    the database and needs free space about its size, so call it before serving traffic
    (``migrations/env.py``) and outside any transaction.

    Raises:
        Exception: Whatever the driver raises, e.g. when the disk is full.
    """
    cursor = connection.connection.dbapi_connection.cursor()  # type: ignore[union-attr]
    try:
        cursor.execute("PRAGMA auto_vacuum")
        [mode] = cursor.fetchone() or (None,)
        if mode == INCREMENTAL:
            return False
        cursor.execute("PRAGMA auto_vacuum=INCREMENTAL")
        cursor.execute("VACUUM")
        return True
    finally:
        cursor.close()


async def reclaim_free_pages(engine: AsyncEngine) -> int:
    """Return the free pages of the database to the file system; return how many.

    Does nothing (and returns 0) when the database is not in incremental mode yet.
    """
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        if driver is None:  # pragma: no cover - only for an invalidated connection
            return 0
        [(mode,)] = await driver.execute_fetchall("PRAGMA auto_vacuum")
        if mode != INCREMENTAL:
            return 0
        [(before,)] = await driver.execute_fetchall("PRAGMA freelist_count")
        # Each step of this pragma frees one page; fetching all rows runs it to the end.
        await driver.execute_fetchall("PRAGMA incremental_vacuum")
        [(after,)] = await driver.execute_fetchall("PRAGMA freelist_count")
        return int(before) - int(after)


async def checkpoint_wal(engine: AsyncEngine) -> None:
    """Fold the write-ahead log into the database file and empty it (``TRUNCATE``).

    Called after a purge batch and after the retention sweep (WP-A3): with ``secure_delete`` the
    purged values are overwritten in the pages, and this makes sure no older copy of those pages
    stays in the ``-wal`` file. When a reader holds the log open the checkpoint is partial;
    the next call finishes it. Blocks on disk and snapshots are the host's concern
    (``docs/dataflow.md``).
    """
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        if driver is None:  # pragma: no cover - only for an invalidated connection
            return
        await driver.execute_fetchall("PRAGMA wal_checkpoint(TRUNCATE)")


def storage_limits(settings: Settings) -> StorageLimits:
    """Return the storage grade boundaries configured in ``settings``."""
    return StorageLimits(
        low_free_percent=settings.storage_low_free_percent,
        critical_free_percent=settings.storage_critical_free_percent,
        database_max_bytes=None
        if settings.database_max_mb is None
        else settings.database_max_mb * MIB,
    )


def measure_storage(database_url: str) -> StorageUsage | None:
    """Measure the database file (with its WAL) and the file system it lives on.

    Returns:
        The measurement, or ``None`` when the database is not a SQLite file or cannot be read.
    """
    try:
        path = database_path(database_url)
        size = path.stat().st_size + _size(path.with_name(f"{path.name}-wal"))
        disk = shutil.disk_usage(path.parent)
    except (ValueError, OSError):
        return None
    return StorageUsage(free_bytes=disk.free, total_bytes=disk.total, database_bytes=size)


def _size(path: Path) -> int:
    """Return the size of ``path``, or 0 when it does not exist."""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0
