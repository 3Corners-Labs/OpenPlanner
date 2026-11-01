"""Worker housekeeping (ADR 0040): storage watch with log and event, hourly page reclaim."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.adapters.db.engine import create_engine
from app.domain.retention_sweep import SweepResult
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


# --- archived groups (ADR 0084 step 3) --------------------------------------------------------


class Eraser:
    """Counts how often housekeeping asked it to erase; fails on demand."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def __call__(self) -> int:
        self.calls += 1
        if self.fail:
            raise RuntimeError("database locked")
        return 2


async def test_archived_groups_are_swept_once_a_day(engine: AsyncEngine) -> None:
    setup = Setup(engine)
    eraser = Eraser()
    setup.housekeeping = Housekeeping(
        engine, setup.uow, setup.clock, StorageLimits(), setup.disk, erase=eraser
    )

    with capture_logs() as logs:
        await setup.housekeeping.run()
        setup.clock.advance(timedelta(hours=23))
        await setup.housekeeping.run()
        setup.clock.advance(timedelta(hours=1))
        await setup.housekeeping.run()

    assert eraser.calls == 2
    assert [e["event"] for e in logs if e["event"].startswith("group.")] == [
        "group.erase_done",
        "group.erase_done",
    ]


async def test_a_failing_erase_never_stops_housekeeping(engine: AsyncEngine) -> None:
    setup = Setup(engine)
    eraser = Eraser()
    eraser.fail = True
    setup.housekeeping = Housekeeping(
        engine, setup.uow, setup.clock, StorageLimits(), setup.disk, erase=eraser
    )

    with capture_logs() as logs:
        await setup.housekeeping.run()

    assert [e["event"] for e in logs if e["event"].startswith("group.")] == ["group.erase_failed"]
    assert eraser.calls == 1


# --- retention sweep (WP-A1) ---------------------------------------------------------------


class Sweeper:
    """Counts how often housekeeping asked for a sweep and returns what the test sets."""

    def __init__(self, result: SweepResult) -> None:
        self.calls = 0
        self.result = result
        self.fail = False

    async def __call__(self) -> SweepResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("database locked")
        return self.result


def _sweeping(engine: AsyncEngine, sweeper: Sweeper) -> Setup:
    setup = Setup(engine)
    setup.housekeeping = Housekeeping(
        engine, setup.uow, setup.clock, StorageLimits(), setup.disk, sweep=sweeper
    )
    return setup


async def _sweep_logs(setup: Setup) -> list[str]:
    with capture_logs() as logs:
        await setup.housekeeping.run()
    return [f"{e['log_level']}:{e['event']}" for e in logs if e["event"].startswith("retention")]


async def test_the_retention_sweep_runs_once_a_day(engine: AsyncEngine) -> None:
    sweeper = Sweeper(SweepResult())
    setup = _sweeping(engine, sweeper)

    await _sweep_logs(setup)
    setup.clock.advance(timedelta(hours=23))
    await _sweep_logs(setup)
    assert sweeper.calls == 1
    setup.clock.advance(timedelta(hours=1))
    await _sweep_logs(setup)
    assert sweeper.calls == 2


async def test_a_quiet_sweep_logs_nothing(engine: AsyncEngine) -> None:
    assert await _sweep_logs(_sweeping(engine, Sweeper(SweepResult()))) == []


async def test_a_sweep_that_purged_logs_counts_and_a_backlog_raises_the_alert(
    engine: AsyncEngine,
) -> None:
    recent = Sweeper(SweepResult(overdue=2, purged=2))
    assert await _sweep_logs(_sweeping(engine, recent)) == ["warning:retention.sweep"]

    old = Sweeper(SweepResult(overdue=2, purged=2, backlog=1))
    assert await _sweep_logs(_sweeping(engine, old)) == [
        "warning:retention.sweep",
        "error:retention.backlog",
    ]


async def test_a_failing_sweep_is_logged_and_never_stops_housekeeping(
    engine: AsyncEngine,
) -> None:
    sweeper = Sweeper(SweepResult())
    sweeper.fail = True

    assert await _sweep_logs(_sweeping(engine, sweeper)) == ["error:retention.sweep_failed"]


async def test_a_sweep_that_purged_empties_the_wal_and_a_quiet_one_does_not(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoints: list[AsyncEngine] = []

    async def record(target: AsyncEngine) -> None:
        checkpoints.append(target)

    monkeypatch.setattr("app.jobs.housekeeping.checkpoint_wal", record)

    await _sweep_logs(_sweeping(engine, Sweeper(SweepResult())))
    assert checkpoints == []

    await _sweep_logs(_sweeping(engine, Sweeper(SweepResult(overdue=1, purged=1))))
    assert checkpoints == [engine]


async def test_a_failing_checkpoint_is_logged_and_never_stops_housekeeping(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(_engine: AsyncEngine) -> None:
        raise RuntimeError("database locked")

    monkeypatch.setattr("app.jobs.housekeeping.checkpoint_wal", fail)
    setup = _sweeping(engine, Sweeper(SweepResult(overdue=1, purged=1)))

    with capture_logs() as logs:
        await setup.housekeeping.run()

    assert "retention.checkpoint_failed" in [e["event"] for e in logs]


# --- admin sessions (ADR 0098 step 2) --------------------------------------------------------


class SessionCleaner:
    """Counts how often housekeeping asked to delete sessions; fails on demand."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def __call__(self) -> int:
        self.calls += 1
        if self.fail:
            raise RuntimeError("database locked")
        return 3


def _with_cleaner(setup: Setup, engine: AsyncEngine, cleaner: SessionCleaner) -> None:
    setup.housekeeping = Housekeeping(
        engine, setup.uow, setup.clock, StorageLimits(), setup.disk, clean_sessions=cleaner
    )


async def test_ended_and_expired_sessions_are_cleaned_once_an_hour(engine: AsyncEngine) -> None:
    setup = Setup(engine)
    cleaner = SessionCleaner()
    _with_cleaner(setup, engine, cleaner)

    with capture_logs() as logs:
        await setup.housekeeping.run()
        setup.clock.advance(timedelta(minutes=59))
        await setup.housekeeping.run()
        setup.clock.advance(timedelta(minutes=1))
        await setup.housekeeping.run()

    assert cleaner.calls == 2
    assert [(e["event"], e["sessions"]) for e in logs if e["event"].startswith("sessions.")] == [
        ("sessions.cleaned", 3),
        ("sessions.cleaned", 3),
    ]


async def test_a_failing_session_clean_never_stops_housekeeping(engine: AsyncEngine) -> None:
    setup = Setup(engine)
    cleaner = SessionCleaner()
    cleaner.fail = True
    _with_cleaner(setup, engine, cleaner)

    with capture_logs() as logs:
        await setup.housekeeping.run()

    assert [e["event"] for e in logs if e["event"].startswith("sessions.")] == [
        "sessions.clean_failed"
    ]
