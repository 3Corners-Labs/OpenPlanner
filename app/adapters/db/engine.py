"""Async SQLAlchemy engine factory and database reachability probe.

SQLite in WAL mode (HANDOVER B2; Postgres is not a target). The Python driver's implicit
transaction handling is switched off so that we emit ``BEGIN`` ourselves: a plain ``BEGIN``
for reads and ``BEGIN IMMEDIATE`` for booking transactions, selected per connection with the
``sqlite_begin`` execution option (see ``app.adapters.db.uow``). A new database is created in
incremental auto-vacuum mode, so pages freed by pruning can go back to the disk (ADR 0040,
``app.adapters.db.maintenance``). ``secure_delete`` is on for every connection, so purged
personal data is overwritten in the database file and not merely marked free. Every engine hides
bound parameters (``hide_parameters``), so a failing insert of a booking leaves no name or
address in the exception text (ASVS V16.2.5). This factory is the only one: the web process, the
worker, the CLI and Alembic all use it.
A statement slower than ``set_slow_query_ms`` leaves a
``sql.slow`` trace line with its kind and first table, never its text (ADR 0085 point 9).
"""

from functools import partial
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import structlog
from sqlalchemy import Connection, event, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.logging import tracer

SQLITE_BUSY_TIMEOUT_MS = 5000
"""How long a SQLite writer waits for a lock before failing (milliseconds)."""

BEGIN_MODE_OPTION = "sqlite_begin"
"""Execution option that selects the ``BEGIN`` variant of the next transaction."""

BeginMode = Literal["DEFERRED", "IMMEDIATE"]

log = structlog.get_logger()

_SLOW_QUERY_MS: list[int] = [200]
"""Threshold of ``sql.slow`` (``SQL_SLOW_MS``, class C); a list so it can change live."""

_STARTED = "openplanner_started"
"""Key under which the start of a statement is kept on its execution context."""


def set_slow_query_ms(milliseconds: int) -> None:
    """Change the ``sql.slow`` threshold live (settings refresh of web and worker)."""
    _SLOW_QUERY_MS[0] = milliseconds


def _before_execute(
    _conn: Connection, _cursor: Any, _statement: str, _params: Any, context: Any, _many: bool
) -> None:
    """Note when the statement starts."""
    if context is not None:
        setattr(context, _STARTED, perf_counter())


def _after_execute(
    _conn: Connection, _cursor: Any, _statement: str, _params: Any, context: Any, _many: bool
) -> None:
    """Trace the statement's kind and table when it took longer than the threshold."""
    started = getattr(context, _STARTED, None) if context is not None else None
    if started is None:
        return
    ms = int((perf_counter() - started) * 1000)
    if ms < _SLOW_QUERY_MS[0]:
        return
    compiled = getattr(context, "compiled", None)
    statement = getattr(compiled, "statement", None)
    tracer.trace(
        "sql.slow",
        statement=type(statement).__name__ if statement is not None else "raw",
        table=_first_table(statement),
        ms=ms,
    )


def _first_table(statement: Any) -> str:
    """The name of the first table a compiled statement touches, or an empty string."""
    table = getattr(statement, "table", None)
    if table is None:
        froms = getattr(statement, "get_final_froms", None)
        tables = froms() if callable(froms) else []
        table = tables[0] if tables else None
    name = getattr(table, "name", None)
    return str(name) if name is not None else ""


def _sqlite_connect(busy_timeout_ms: int, dbapi_connection: Any, _record: Any) -> None:
    """Disable implicit transactions and apply auto-vacuum, WAL, foreign keys, lock timeout."""
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    # Takes effect only before the first table exists; existing files are converted once.
    cursor.execute("PRAGMA auto_vacuum=INCREMENTAL")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # Fixed privacy rule (WP-A3): deleted and purged content is overwritten with zeros, not left
    # in freed page space. Not a setting: switching it off would defeat the retention rules.
    cursor.execute("PRAGMA secure_delete=ON")
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

    # Fixed privacy rule (ASVS V16.2.5): a failing statement must not carry its bound values
    # (names, addresses) into the exception text and from there into a traceback log.
    engine = create_async_engine(url, pool_pre_ping=True, hide_parameters=True)
    event.listen(engine.sync_engine, "before_cursor_execute", _before_execute)
    event.listen(engine.sync_engine, "after_cursor_execute", _after_execute)
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
