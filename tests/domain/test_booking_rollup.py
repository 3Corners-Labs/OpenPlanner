"""Old purged booking rows are merged into daily counts and deleted (ADR 0102 step 4)."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from app.domain.audit import Outcome
from app.domain.authorization import Scope
from app.domain.booking import BookingStatus
from app.domain.booking_rollup import (
    DayCount,
    RollableBooking,
    is_rollable,
    purged_before,
    tally,
)
from app.domain.booking_rollup_service import ROLLED_UP, BookingRowRollup
from app.domain.job_plan import JobType, PlannedJob
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import ADA, NOW, SLOT_0900, SLOT_0930, book_confirmed

TERM = timedelta(days=730)
DAY = timedelta(days=1)


def _row(**changes: object) -> RollableBooking:
    row = RollableBooking(
        1, 7, date(2026, 1, 5), BookingStatus.COMPLETED, NOW - TERM - DAY, False, 0
    )
    return replace(row, **changes)  # type: ignore[arg-type]


def test_a_purged_row_past_the_term_is_rollable() -> None:
    assert is_rollable(_row(), NOW, TERM)


@pytest.mark.parametrize(
    "changes",
    [
        {"purged_at": None},
        {"purged_at": NOW - TERM + DAY},
        {"holds_data": True},
        {"open_jobs": 1},
    ],
)
def test_a_row_that_is_too_young_unpurged_or_busy_is_never_rollable(
    changes: dict[str, object],
) -> None:
    assert not is_rollable(_row(**changes), NOW, TERM)


def test_the_cut_off_is_the_term_before_now() -> None:
    assert purged_before(NOW, TERM) == NOW - TERM


def test_tally_counts_per_group_day_and_status_with_the_latest_purge() -> None:
    early, late = NOW - 2 * TERM, NOW - TERM
    rows = [
        _row(booking_id=1, purged_at=early),
        _row(booking_id=2, purged_at=late),
        _row(booking_id=3, status=BookingStatus.CANCELLED, purged_at=early),
        _row(booking_id=4, group_id=8, purged_at=early),
    ]

    assert tally(rows) == [
        DayCount(7, date(2026, 1, 5), BookingStatus.CANCELLED, 1, early),
        DayCount(7, date(2026, 1, 5), BookingStatus.COMPLETED, 2, late),
        DayCount(8, date(2026, 1, 5), BookingStatus.COMPLETED, 1, early),
    ]


@pytest.fixture
def rollup(uow: InMemoryUnitOfWork, clock: FixedClock) -> BookingRowRollup:
    return BookingRowRollup(uow, clock, lambda: TERM)


async def _booked(bookings: BookingService, start: datetime = SLOT_0900) -> int:
    receipt = await book_confirmed(
        bookings, group_slug="demo", start=start, contact=ADA, locale="nl"
    )
    return receipt.booking.id or 0


async def _purged_booking(
    bookings: BookingService, uow: InMemoryUnitOfWork, start: datetime = SLOT_0900
) -> int:
    booking_id = await _booked(bookings, start)
    async with uow.locked() as tx:
        await tx.purge_personal_data(booking_id, NOW + DAY)
        await tx.delete_pending_jobs(booking_id, set(JobType))
    return booking_id


async def test_a_row_is_kept_until_the_term_has_passed(
    rollup: BookingRowRollup,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    booking_id = await _purged_booking(bookings, uow)
    clock.advance(TERM - 2 * DAY)

    result = await rollup.run()

    assert result.rows == 0
    assert booking_id in uow.state.bookings
    assert uow.state.audit == []


async def test_a_row_past_the_term_becomes_a_count_and_goes_with_its_events(
    rollup: BookingRowRollup,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    booking_id = await _purged_booking(bookings, uow)
    clock.advance(TERM + 2 * DAY)

    result = await rollup.run()

    assert (result.rows, result.day_counts, result.skipped) == (1, 1, 0)
    assert booking_id not in uow.state.bookings
    assert booking_id not in uow.state.booking_events
    [count] = uow.state.day_counts.values()
    assert (count.day, count.status, count.count) == (
        SLOT_0900.date(),
        BookingStatus.CONFIRMED,
        1,
    )


async def test_the_run_is_idempotent(
    rollup: BookingRowRollup,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    await _purged_booking(bookings, uow)
    clock.advance(TERM + 2 * DAY)

    first, second = await rollup.run(), await rollup.run()

    assert (first.rows, second.rows) == (1, 0)
    assert sum(c.count for c in uow.state.day_counts.values()) == 1
    assert len([e for e in uow.state.audit if e.action == ROLLED_UP]) == 1


async def test_a_report_gives_the_same_total_before_and_after_the_merge(
    rollup: BookingRowRollup,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    await _purged_booking(bookings, uow, SLOT_0900)
    await _purged_booking(bookings, uow, SLOT_0930)
    group_id = next(iter(uow.state.bookings.values())).group_id
    clock.advance(TERM + 2 * DAY)
    async with uow.read() as tx:
        before = await tx.booking_totals(group_id)

    await rollup.run()

    async with uow.read() as tx:
        after = await tx.booking_totals(group_id)
    assert uow.state.bookings == {}
    assert before == after == {BookingStatus.CONFIRMED: 2}


async def test_a_row_with_personal_data_or_an_open_job_is_never_deleted(
    rollup: BookingRowRollup,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    holding = await _booked(bookings, SLOT_0900)
    uow.state.purged_at[holding] = NOW + DAY  # a purge moment, but the data is still there
    busy = await _purged_booking(bookings, uow, SLOT_0930)
    uow.state.jobs[busy] = [PlannedJob(JobType.PURGE_PII, NOW, None)]
    clock.advance(TERM + 2 * DAY)

    result = await rollup.run()

    assert (result.rows, result.skipped) == (0, 2)
    assert {holding, busy} <= set(uow.state.bookings)
    assert uow.state.day_counts == {}


async def test_the_event_holds_counts_and_no_identifiers(
    rollup: BookingRowRollup,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    await _purged_booking(bookings, uow)
    clock.advance(TERM + 2 * DAY)

    await rollup.run()

    [event] = [e for e in uow.state.audit if e.action == ROLLED_UP]
    assert event.outcome is Outcome.SUCCESS
    assert event.detail == "rows:1,days:1,skipped:0"
    assert event.booking_id is None
    assert ADA.email not in repr(event)


async def test_the_retention_overview_still_shows_the_last_removal_after_the_merge(
    rollup: BookingRowRollup,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    await _purged_booking(bookings, uow)
    clock.advance(TERM + 2 * DAY)
    scope = Scope(everything=True)

    async def last_removed() -> list[datetime | None]:
        async with uow.read() as tx:
            facts = await tx.list_retention(scope, clock.now())
        return [c.last_purged_at for c in facts.groups if c.group.group.slug == "demo"]

    before = await last_removed()
    await rollup.run()

    assert await last_removed() == before == [NOW + DAY]
