"""Storage limits (ADR 0040): grading free space and database size, reporting changes."""

import pytest

from app.domain.audit import Outcome, Source
from app.domain.storage import (
    MIB,
    STORAGE_EVENT,
    StorageGrade,
    StorageLimits,
    StorageUsage,
    StorageWatch,
    assess,
)
from app.domain.system_log import SystemLog
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW

GIB = 1024 * MIB


def usage(free_percent: float, database_mib: int = 10) -> StorageUsage:
    total = 100 * GIB
    return StorageUsage(
        free_bytes=int(total * free_percent / 100),
        total_bytes=total,
        database_bytes=database_mib * MIB,
    )


@pytest.mark.parametrize(
    ("free", "expected"),
    [
        (50, (StorageGrade.OK, "")),
        (15, (StorageGrade.OK, "")),
        (14.9, (StorageGrade.LOW, "free_space_low")),
        (5, (StorageGrade.LOW, "free_space_low")),
        (4.9, (StorageGrade.CRITICAL, "free_space_critical")),
        (0, (StorageGrade.CRITICAL, "free_space_critical")),
    ],
)
def test_free_space_is_graded_against_the_limits(
    free: float, expected: tuple[StorageGrade, str]
) -> None:
    assert assess(usage(free), StorageLimits()) == expected


def test_a_database_above_its_maximum_is_low() -> None:
    limits = StorageLimits(database_max_bytes=100 * MIB)

    assert assess(usage(50, database_mib=100), limits) == (StorageGrade.OK, "")
    assert assess(usage(50, database_mib=101), limits) == (
        StorageGrade.LOW,
        "database_too_large",
    )


def test_critical_free_space_wins_over_a_large_database() -> None:
    limits = StorageLimits(database_max_bytes=MIB)

    assert assess(usage(1), limits) == (StorageGrade.CRITICAL, "free_space_critical")


def test_an_empty_file_system_counts_as_full() -> None:
    assert StorageUsage(free_bytes=0, total_bytes=0, database_bytes=0).free_percent == 0


def test_limits_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="critical"):
        StorageLimits(low_free_percent=5, critical_free_percent=5)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(State())


def watch(uow: InMemoryUnitOfWork) -> StorageWatch:
    return StorageWatch(SystemLog(uow, FixedClock(NOW)), StorageLimits())


async def test_a_healthy_start_records_nothing(uow: InMemoryUnitOfWork) -> None:
    check = await watch(uow).observe(usage(50))

    assert (check.grade, check.changed) == (StorageGrade.OK, False)
    assert uow.state.audit == []


async def test_every_change_of_grade_is_one_global_system_event(uow: InMemoryUnitOfWork) -> None:
    storage = watch(uow)

    for free in (50, 10, 9, 3, 3, 40):
        await storage.observe(usage(free, database_mib=7))

    assert [(e.outcome, e.reason, e.detail) for e in uow.state.audit] == [
        (Outcome.FAILURE, "free_space_low", "free_percent:10,database_mb:7"),
        (Outcome.FAILURE, "free_space_critical", "free_percent:3,database_mb:7"),
        (Outcome.SUCCESS, "", "free_percent:40,database_mb:7"),
    ]
    for event in uow.state.audit:
        assert (event.action, event.source) == (STORAGE_EVENT, Source.SYSTEM)
        assert (event.organisation, event.group, event.booking_id) == (None, None, None)


class FlakySystemLog(SystemLog):
    """Fails the first write, like a locked database."""

    failed = False

    async def record(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        if not self.failed:
            self.failed = True
            raise RuntimeError("database is locked")
        await super().record(*args, **kwargs)  # type: ignore[arg-type]


async def test_a_failed_record_is_tried_again_next_time(uow: InMemoryUnitOfWork) -> None:
    storage = StorageWatch(FlakySystemLog(uow, FixedClock(NOW)), StorageLimits())

    with pytest.raises(RuntimeError):
        await storage.observe(usage(3))
    check = await storage.observe(usage(3))

    assert check.changed
    assert [e.reason for e in uow.state.audit] == ["free_space_critical"]
