"""Housekeeping lets the rebooking assistant propose every five minutes (ADR 0057 point 6)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.adapters.db.engine import create_engine
from app.domain.storage import StorageLimits
from app.jobs.housekeeping import AUTO_EVERY, Housekeeping
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.fakes.storage import Disk
from tests.support.database import migrated_database
from tests.support.domain import NOW


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "housekeeping-auto-proposals")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    yield engine
    await engine.dispose()


async def test_it_runs_once_per_interval_and_a_failure_never_stops_housekeeping(
    engine: AsyncEngine,
) -> None:
    clock = FixedClock(NOW)
    calls: list[int] = []

    async def propose() -> int:
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("database locked")
        return 2

    housekeeping = Housekeeping(
        engine, InMemoryUnitOfWork(State()), clock, StorageLimits(), Disk(), auto_proposals=propose
    )

    with capture_logs() as logs:
        await housekeeping.run()
        clock.advance(AUTO_EVERY - timedelta(seconds=1))
        await housekeeping.run()
        clock.advance(timedelta(seconds=1))
        await housekeeping.run()
        clock.advance(AUTO_EVERY)
        await housekeeping.run()

    assert len(calls) == 3
    events = [(e["event"], e.get("bookings")) for e in logs if e["event"].startswith("reschedule.")]
    assert events == [
        ("reschedule.auto_sent", 2),
        ("reschedule.auto_sent", 2),
        ("reschedule.auto_failed", None),
    ]
