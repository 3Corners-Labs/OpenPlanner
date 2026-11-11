"""The two backends of the flow contract tests, opened the same way (ADR 0121).

``open_backend("sql" | "fake", database_url)`` yields the unit of work on a migrated database or
the in-memory fake, the engine (SQL only), and one way to forget every legacy pool and flow, as a
database that an older image wrote to looks.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import CapacityPoolRow, FlowRevisionRow, ServiceFlowRow
from app.adapters.db.uow import SqlUnitOfWork
from tests.fakes.persistence import InMemoryUnitOfWork

BACKENDS = ["sql", "fake"]
"""The ``params`` of a ``world`` fixture."""


@dataclass
class Backend:
    """A unit of work on one backend."""

    uow: Any
    engine: AsyncEngine | None
    forget: Callable[[], Awaitable[None]]
    """Delete every pool and flow."""


@asynccontextmanager
async def open_backend(kind: str, database_url: str) -> AsyncIterator[Backend]:
    """Open the ``sql`` or ``fake`` backend; the engine is disposed on exit."""
    if kind == "fake":
        fake = InMemoryUnitOfWork()

        async def forget_in_fake() -> None:
            for table in (fake.state.flow_revisions, fake.state.flows, fake.state.pools):
                table.clear()

        yield Backend(fake, None, forget_in_fake)
        return
    engine = create_engine(database_url, busy_timeout_ms=200)

    async def forget_in_sql() -> None:
        async with AsyncSession(engine) as session, session.begin():
            for row in (FlowRevisionRow, ServiceFlowRow, CapacityPoolRow):
                await session.execute(delete(row))

    try:
        yield Backend(SqlUnitOfWork(engine), engine, forget_in_sql)
    finally:
        await engine.dispose()
