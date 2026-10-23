"""Async SQLAlchemy engine factory and database reachability probe.

SQLite in WAL mode (HANDOVER B2; Postgres is not a target). The Python driver's implicit
transaction handling is switched off so that we emit ``BEGIN`` ourselves: a plain ``BEGIN``
for reads and ``BEGIN IMMEDIATE`` for booking transactions, selected per connection with the
``sqlite_begin`` execution option (see ``app.adapters.db.uow``).
"""

from functools import partial
from pathlib import Path
from typing import Any, Literal

import structlog
from sqlalchemy import Connection, event, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SQLITE_BUSY_TIMEOUT_MS = 5000
"""How long a SQLite writer waits for a lock before failing (milliseconds)."""

BEGIN_MODE_OPTION = "sqlite_begin"
"""Execution option that selects the ``BEGIN`` variant of the next transaction."""

BeginMode = Literal["DEFERRED", "IMMEDIATE"]

log = structlog.get_logger()


def _sqlite_connect(busy_timeout_ms: int, dbapi_connection: Any, _record: Any) -> None:
    """Disable implicit transactions and apply WAL, foreign keys and the lock timeout."""
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    cursor.close()


def _sqlite_begin(conn: Connection) -> None:
    """Emit ``BEGIN`` or ``BEGIN IMMEDIATE`` according to the connection's options."""
    mode: BeginMode = conn.get_execution_options().get(BEGIN_MODE_OPTION, "DEFERRED")
    if mode not in ("DEFERRED", "IMMEDIATE"):
        raise ValueError(f"unsupported {BEGIN_MODE_OPTION}: {mode!r}")
    conn.exec_driver_sql(f"BEGIN {mode}")


def create_engine(database_url: str, busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS) -> AsyncEngine:
    """Create the async engine for ``database_url``.

    For a file-based SQLite URL the parent directory is created; every connection gets the
    pragmas and explicit transaction control described in the module docstring.

    Args:
        database_url: SQLAlchemy URL, e.g. ``sqlite+aiosqlite:///data/booking.db``.
        busy_timeout_ms: How long a writer waits for the lock (tests use a short value).

    Returns:
        A pooled ``AsyncEngine``. The caller owns it and must ``dispose()`` it.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", partial(_sqlite_connect, busy_timeout_ms))
        event.listen(engine.sync_engine, "begin", _sqlite_begin)
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
