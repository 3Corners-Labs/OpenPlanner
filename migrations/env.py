"""Alembic environment: runs migrations with the application's engine and metadata.

The database URL comes from ``DATABASE_URL`` via ``app.config.Settings``, never from
``alembic.ini``. SQLite uses batch mode so that ALTER operations are reversible.
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


def _run(connection: Connection) -> None:
    """Configure the migration context on ``connection`` and run pending migrations."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


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
