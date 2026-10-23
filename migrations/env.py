"""Alembic environment: runs migrations with the application's engine and metadata.

The database URL comes from ``DATABASE_URL`` via ``app.config.Settings``, never from
``alembic.ini``. SQLite uses batch mode so that ALTER operations are reversible.

Batch mode rebuilds a table (create copy, move rows, drop old). With foreign keys enforced,
dropping a table that other rows reference fails, so on SQLite foreign keys are switched off
for the migration (the pragma only works outside a transaction) and ``PRAGMA
foreign_key_check`` must come back empty before the migration commits.
"""

import asyncio
from typing import Any

from alembic import context
from sqlalchemy.engine import Connection

from app.adapters.db.engine import create_engine
from app.adapters.db.models import Base
from app.adapters.db.types import UTCDateTime
from app.config import Settings

target_metadata = Base.metadata


def _render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render application column types as plain SQLAlchemy types in generated migrations.

    Migrations must not import application code: a later change to ``UTCDateTime`` would
    otherwise break old migrations. The storage type is the same ``DateTime``.
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    return False


def _pragma(connection: Connection, statement: str) -> None:
    """Run a SQLite pragma on the raw connection, outside any transaction."""
    cursor = connection.connection.dbapi_connection.cursor()  # type: ignore[union-attr]
    try:
        cursor.execute(statement)
    finally:
        cursor.close()


def _run(connection: Connection) -> None:
    """Configure the migration context on ``connection`` and run pending migrations."""
    sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=sqlite,
        compare_type=True,
        render_item=_render_item,
    )
    if sqlite:
        _pragma(connection, "PRAGMA foreign_keys=OFF")
    try:
        with context.begin_transaction():
            context.run_migrations()
            if sqlite and connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise RuntimeError("migration left rows that violate a foreign key")
    finally:
        if sqlite:
            _pragma(connection, "PRAGMA foreign_keys=ON")


async def _run_online() -> None:
    """Open an engine for the configured database and migrate it."""
    engine = create_engine(Settings().database_url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
