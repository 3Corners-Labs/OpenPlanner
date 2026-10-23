"""Async SQLAlchemy engine factory and database reachability probe.

SQLite (the default, HANDOVER B2) gets its pragmas on every new connection. Postgres works
through the same ``DATABASE_URL`` without code changes.
"""

from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import event, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SQLITE_BUSY_TIMEOUT_MS = 5000
"""How long a SQLite writer waits for a lock before failing (milliseconds)."""

log = structlog.get_logger()


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """Apply WAL, foreign keys and lock timeout to a fresh SQLite connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def create_engine(database_url: str) -> AsyncEngine:
    """Create the async engine for ``database_url``.

    For a file-based SQLite URL the parent directory is created, and every connection gets
    the pragmas from ``_sqlite_pragmas``.

    Args:
        database_url: SQLAlchemy URL, e.g. ``sqlite+aiosqlite:///data/booking.db``.

    Returns:
        A pooled ``AsyncEngine``. The caller owns it and must ``dispose()`` it.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
    return engine


async def ping(engine: AsyncEngine) -> None:
    """Run ``SELECT 1``.

    Raises:
        Exception: Whatever the driver raises when the database is unreachable.
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def is_reachable(engine: AsyncEngine) -> bool:
    """Return whether the database answers, logging a failure without its details.

    Only the exception type is logged: driver messages can contain file paths or
    connection strings with credentials.
    """
    try:
        await ping(engine)
    except Exception as exc:
        log.warning("database.unreachable", error=type(exc).__name__)
        return False
    return True
