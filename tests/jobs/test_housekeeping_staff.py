"""Housekeeping erases staff records past their retention once a day (ADR 0046 step 2)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.adapters.db.engine import create_engine
from app.domain.storage import StorageLimits
from app.jobs.housekeeping import Housekeeping
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.jobs.test_housekeeping import Disk
from tests.support.database import migrated_database
from tests.support.domain import NOW


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "housekeeping-staff")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    yield engine
    await engine.dispose()


class StaffEraser:
    """Counts how often housekeeping asked to erase staff; fails on demand."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def __call__(self) -> int:
        self.calls += 1
        if self.fail:
            raise RuntimeError("database locked")
        return 2


def _housekeeping(engine: AsyncEngine, clock: FixedClock, eraser: StaffEraser) -> Housekeeping:
    return Housekeeping(
        engine,
        InMemoryUnitOfWork(State()),
        clock,
        StorageLimits(),
        Disk(),
        erase_staff=eraser,
    )


async def test_staff_are_erased_once_a_day(engine: AsyncEngine) -> None:
    clock, eraser = FixedClock(NOW), StaffEraser()
    housekeeping = _housekeeping(engine, clock, eraser)

    with capture_logs() as logs:
        await housekeeping.run()
        clock.advance(timedelta(hours=23))
        await housekeeping.run()
        clock.advance(timedelta(hours=1))
        await housekeeping.run()

    assert eraser.calls == 2
    assert [(e["event"], e["staff"]) for e in logs if e["event"] == "staff.erased"] == [
        ("staff.erased", 2),
        ("staff.erased", 2),
    ]


async def test_a_failing_staff_erase_never_stops_housekeeping(engine: AsyncEngine) -> None:
    clock, eraser = FixedClock(NOW), StaffEraser()
    eraser.fail = True
    housekeeping = _housekeeping(engine, clock, eraser)

    with capture_logs() as logs:
        await housekeeping.run()

    assert [e["event"] for e in logs if e["event"].startswith("staff.")] == ["staff.erase_failed"]
