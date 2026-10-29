"""Worker housekeeping (ADR 0040): storage watch with log and event, hourly page reclaim."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.adapters.db.engine import create_engine
from app.domain.storage import MIB, StorageLimits, StorageUsage
from app.jobs.housekeeping import Housekeeping
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.database import migrated_database


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "housekeeping")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    yield engine
    await engine.dispose()


class Disk:
    """A data volume whose free percentage the test sets."""

    free_percent: float = 50

    def __call__(self) -> StorageUsage:
        return StorageUsage(int(self.free_percent * MIB), 100 * MIB, 3 * MIB)


class Setup:
    def __init__(self, engine: AsyncEngine, uow: InMemoryUnitOfWork | None = None) -> None:
        self.clock = FixedClock()
        self.disk = Disk()
        self.uow = uow or InMemoryUnitOfWork(State())
        self.housekeeping = Housekeeping(engine, self.uow, self.clock, StorageLimits(), self.disk)

    async def run(self, free_percent: float, after: timedelta = timedelta()) -> list[str]:
        self.clock.advance(after)
        self.disk.free_percent = free_percent
        with capture_logs() as logs:
            await self.housekeeping.run()
        return [f"{e['log_level']}:{e['event']}" for e in logs if e["event"].startswith("storage")]


async def test_a_healthy_volume_is_quiet(engine: AsyncEngine) -> None:
    setup = Setup(engine)

    assert await setup.run(50) == []
    assert setup.uow.state.audit == []


async def test_a_degraded_volume_is_logged_on_change_then_hourly(engine: AsyncEngine) -> None:
    setup = Setup(engine)

    assert await setup.run(10) == ["warning:storage.degraded"]
    assert await setup.run(10, after=timedelta(minutes=59)) == []
    assert await setup.run(10, after=timedelta(minutes=1)) == ["warning:storage.degraded"]
    assert await setup.run(3) == ["error:storage.degraded"]
    assert await setup.run(40) == ["info:storage.recovered"]
    assert await setup.run(40, after=timedelta(hours=2)) == []

    assert [e.reason for e in setup.uow.state.audit] == [
        "free_space_low",
        "free_space_critical",
        "",
    ]


class BrokenUnitOfWork(InMemoryUnitOfWork):
    """Fails every write, like a disk that is full."""

    def locked(self):  # type: ignore[no-untyped-def]
        raise OSError("disk full")


async def test_the_log_does_not_depend_on_the_database(engine: AsyncEngine) -> None:
    setup = Setup(engine, BrokenUnitOfWork(State()))

    assert await setup.run(2) == ["error:storage.degraded", "warning:storage.record_failed"]


async def test_an_unknown_volume_is_skipped(engine: AsyncEngine) -> None:
    housekeeping = Housekeeping(
        engine, InMemoryUnitOfWork(State()), FixedClock(), StorageLimits(), lambda: None
    )

    with capture_logs() as logs:
        await housekeeping.run()

    assert [e for e in logs if e["event"].startswith("storage")] == []


async def _free_pages(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("PRAGMA freelist_count"))).scalar_one())


async def _fill_and_empty(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS filler (a TEXT)"))
        for _ in range(200):
            await conn.execute(text("INSERT INTO filler VALUES (:a)"), {"a": "x" * 1000})
        await conn.execute(text("DELETE FROM filler"))


async def test_freed_pages_are_reclaimed_once_per_hour(engine: AsyncEngine) -> None:
    setup = Setup(engine)

    await _fill_and_empty(engine)
    await setup.run(50)
    assert await _free_pages(engine) == 0

    await _fill_and_empty(engine)
    await setup.run(50, after=timedelta(minutes=30))
    assert await _free_pages(engine) > 0

    await setup.run(50, after=timedelta(minutes=30))
    assert await _free_pages(engine) == 0


async def test_at_grade_critical_the_brake_ends_diagnostic_mode(engine: AsyncEngine) -> None:
    # ADR 0040, part 4: the emergency brake runs every loop at critical, never otherwise.
    reasons: list[str] = []

    async def brake(reason: str) -> int:
        reasons.append(reason)
        return 1

    clock, disk = FixedClock(), Disk()
    housekeeping = Housekeeping(
        engine, InMemoryUnitOfWork(State()), clock, StorageLimits(), disk, brake
    )

    for free in (10, 3, 3, 40):
        disk.free_percent = free
        with capture_logs() as logs:
            await housekeeping.run()

    assert reasons == ["free_space_critical", "free_space_critical"]
    assert not [e for e in logs if e["event"] == "diagnostics.braked"]  # recovered: no brake


async def test_a_failing_brake_never_stops_housekeeping(engine: AsyncEngine) -> None:
    async def brake(_reason: str) -> int:
        raise RuntimeError("disk full")

    disk = Disk()
    disk.free_percent = 3
    housekeeping = Housekeeping(
        engine, InMemoryUnitOfWork(State()), FixedClock(), StorageLimits(), disk, brake
    )

    with capture_logs() as logs:
        await housekeeping.run()

    assert "diagnostics.brake_failed" in [e["event"] for e in logs]
