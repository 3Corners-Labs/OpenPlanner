"""The rollup and backfill services against the in-memory store: batches, caps, flags."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain import usage_backfill_service, usage_rollup_service
from app.domain.booking import BookingKind, BookingStatus
from app.domain.scheduling import DispatchKind
from app.domain.usage import FactKind, UsageFact, hour_of
from app.domain.usage_backfill import BackfillBooking
from app.domain.usage_backfill_service import UsageBackfill
from app.domain.usage_rollup_service import UsageRollup
from tests.fakes.clock import FixedClock
from tests.fakes.usage import InMemoryUsageUnitOfWork, UsageData

NOW = datetime(2026, 11, 20, 12, 30, tzinfo=UTC)
HOUR = hour_of(NOW - timedelta(days=1))


def _rollup(uow: InMemoryUsageUnitOfWork, clock: FixedClock) -> UsageRollup:
    return UsageRollup(uow, clock, lambda: timedelta(minutes=15), lambda: 35, lambda: 25)


async def _facts(uow: InMemoryUsageUnitOfWork, count: int) -> None:
    async with uow.locked() as store:
        for _ in range(count):
            await store.record_usage(UsageFact(FactKind.BOOKING_SLOTTED, HOUR, 1, 2))


async def test_the_rollup_adds_facts_up_and_moves_the_watermark() -> None:
    uow = InMemoryUsageUnitOfWork()
    await _facts(uow, 3)

    run = await _rollup(uow, FixedClock(NOW)).run_if_due()

    assert run is not None
    assert (run.facts, run.cells, run.more) == (3, 1, False)
    assert [c.count for c in uow.data.cells] == [3]
    assert uow.data.last_event_id == 3


async def test_a_run_that_left_work_is_due_again_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(usage_rollup_service, "BATCH_SIZE", 2)
    monkeypatch.setattr(usage_rollup_service, "MAX_BATCHES", 1)
    uow = InMemoryUsageUnitOfWork()
    await _facts(uow, 3)
    rollup = _rollup(uow, FixedClock(NOW))

    first = await rollup.run_if_due()
    second = await rollup.run_if_due()
    third = await rollup.run_if_due()

    assert first is not None
    assert second is not None
    assert (first.more, second.more, third) == (True, False, None)


async def test_a_full_prune_batch_asks_for_another_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage_rollup_service, "PRUNE_BATCH", 1)
    uow = InMemoryUsageUnitOfWork()
    async with uow.locked() as store:
        old = hour_of(NOW - timedelta(days=60))
        for _ in range(2):
            await store.record_usage(UsageFact(FactKind.BOOKING_SLOTTED, old, 1, 2))

    run = await _rollup(uow, FixedClock(NOW)).run_if_due()

    assert run is not None
    assert (run.pruned.events, run.more) == (1, True)


def _booking(number: int) -> BackfillBooking:
    return BackfillBooking(
        id=number,
        organisation_id=1,
        group_id=2,
        dispatch_kind=DispatchKind.CONTACT_CENTER,
        kind=BookingKind.SCHEDULED,
        locale="nl",
        status=BookingStatus.CONFIRMED,
        created_at=NOW - timedelta(days=9),
        start=NOW - timedelta(days=5),
    )


def _backfill(uow: InMemoryUsageUnitOfWork, *, on: bool = True) -> UsageBackfill:
    return UsageBackfill(uow, FixedClock(NOW), lambda: on)


async def test_the_backfill_turns_bookings_into_facts_and_completes() -> None:
    uow = InMemoryUsageUnitOfWork(UsageData(bookings=[_booking(1), _booking(2)]))

    run = await _backfill(uow).run()

    assert (run.bookings, run.facts, run.complete) == (2, 4, True)
    assert uow.data.done == {1, 2}
    assert uow.data.backfilled_at == NOW


async def test_the_worker_pass_is_bounded_and_goes_on_until_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(usage_backfill_service, "BATCH_SIZE", 1)
    monkeypatch.setattr(usage_backfill_service, "MAX_BATCHES", 1)
    uow = InMemoryUsageUnitOfWork(UsageData(bookings=[_booking(1), _booking(2)]))
    backfill = _backfill(uow)

    passes = [await backfill.run_once() for _ in range(4)]

    assert [p.complete for p in passes] == [False, False, True, True]


async def test_a_finished_backfill_does_not_touch_the_store_again() -> None:
    uow = InMemoryUsageUnitOfWork(UsageData(bookings=[_booking(1)]))
    backfill = _backfill(uow)
    await backfill.run_once()
    uow.data.bookings.append(replace(_booking(2)))

    again = await backfill.run_once()

    assert again.complete is True
    assert uow.data.done == {1}


async def test_an_already_completed_backfill_is_noticed_without_running() -> None:
    uow = InMemoryUsageUnitOfWork(UsageData(bookings=[_booking(1)], backfilled_at=NOW))

    run = await _backfill(uow).run_once()

    assert run.complete is True
    assert uow.data.done == set()


async def test_with_reporting_off_nothing_is_done() -> None:
    uow = InMemoryUsageUnitOfWork(UsageData(bookings=[_booking(1)]))

    run = await _backfill(uow, on=False).run()

    assert run.skipped is True
    assert uow.data.done == set()
    assert uow.data.events == []
