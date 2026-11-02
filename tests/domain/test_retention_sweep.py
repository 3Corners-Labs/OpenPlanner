"""The nightly sweep purges what the purge job missed (CLAUDE.md privacy rules, WP-A1)."""

from datetime import timedelta

import pytest

from app.domain.audit import Outcome
from app.domain.authorization import Role
from app.domain.booking import BookingStatus
from app.domain.closure_retention import (
    DEFAULT_CLOSURE_RETENTION,
    RETENTION_AFTER_CLOSURE,
    ClosureRetention,
)
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.job_plan import JobType
from app.domain.retention_sweep import CLOSURE_NOTICE, RetentionSweep
from app.domain.services import BookingService, ContactInput
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import NOTICES
from tests.support.domain import (
    ADA,
    NOW,
    SLOT_0900,
    SLOT_0930,
    book_confirmed,
    book_pending,
)
from tests.support.principals import principal

DAY = timedelta(hours=24)
SUPER = principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
GROUP_ADMIN = principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))


@pytest.fixture
def sweep(uow: InMemoryUnitOfWork, clock: FixedClock) -> RetentionSweep:
    return RetentionSweep(uow, clock)


async def _book(bookings: BookingService, contact: ContactInput = ADA) -> int:
    receipt = await book_confirmed(
        bookings, group_slug="demo", start=SLOT_0900, contact=contact, locale="nl"
    )
    return receipt.booking.id or 0


def _holds_data(uow: InMemoryUnitOfWork, booking_id: int) -> bool:
    return uow.state.bookings[booking_id].contact is not None


async def test_a_booking_past_its_purge_moment_without_a_purge_is_purged(
    sweep: RetentionSweep, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _book(bookings)
    clock.advance(SLOT_0930 - NOW + DAY + timedelta(hours=2))

    result = await sweep.run()

    assert (result.overdue, result.purged, result.failed) == (1, 1, 0)
    assert not _holds_data(uow, booking_id)
    assert uow.state.purged_at[booking_id] == clock.now()
    assert JobType.PURGE_PII not in [j.type for j in uow.state.jobs[booking_id]]  # nothing left


async def test_a_booking_whose_purge_job_failed_is_purged_too(
    sweep: RetentionSweep, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _book(bookings)
    uow.state.jobs[booking_id] = []  # the job ran out of attempts: no pending job any more
    clock.advance(SLOT_0930 - NOW + DAY + timedelta(hours=2))

    assert (await sweep.run()).purged == 1
    assert not _holds_data(uow, booking_id)


async def test_a_purge_that_is_merely_waiting_for_the_worker_is_left_alone(
    sweep: RetentionSweep, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _book(bookings)
    clock.advance(SLOT_0930 - NOW + DAY + timedelta(minutes=30))  # within PURGE_GRACE

    result = await sweep.run()

    assert result.overdue == 0
    assert _holds_data(uow, booking_id)


async def test_the_sweep_is_idempotent(
    sweep: RetentionSweep, bookings: BookingService, clock: FixedClock
) -> None:
    await _book(bookings)
    clock.advance(SLOT_0930 - NOW + 3 * DAY)

    first, second = await sweep.run(), await sweep.run()

    assert first.purged == 1
    assert (second.overdue, second.purged) == (0, 0)


async def test_an_old_backlog_is_reported_and_recorded_without_personal_data(
    sweep: RetentionSweep, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await _book(bookings)
    clock.advance(SLOT_0930 - NOW + 3 * DAY)  # older than 48 h

    result = await sweep.run()

    assert result.backlog == 1
    [event] = [e for e in uow.state.audit if e.action == "retention.sweep"]
    assert event.outcome is Outcome.SUCCESS
    assert event.detail == "overdue:1,purged:1,failed:0,backlog:1"
    assert ADA.email not in repr(event)


async def test_a_quiet_night_writes_no_event(
    sweep: RetentionSweep, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await _book(bookings)

    result = await sweep.run()

    assert (result.overdue, result.backlog) == (0, 0)
    assert uow.state.audit == []


async def test_a_dry_run_counts_per_group_and_changes_nothing(
    sweep: RetentionSweep, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _book(bookings)
    clock.advance(SLOT_0930 - NOW + 3 * DAY)

    result = await sweep.run(dry_run=True)

    assert (result.overdue, result.purged) == (1, 0)
    assert result.per_group == {"demo": 1}
    assert _holds_data(uow, booking_id)
    assert uow.state.audit == []


async def test_an_unconfirmed_booking_is_purged_after_its_deadline_not_its_slot(
    sweep: RetentionSweep, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    receipt = await book_pending(bookings, SLOT_0900, "demo")
    booking = uow.state.bookings[receipt.booking.id or 0]
    assert booking.status is BookingStatus.PENDING_CONFIRMATION
    assert booking.confirm_by is not None
    clock.advance(booking.confirm_by - NOW + DAY + timedelta(hours=2))
    assert clock.now() < booking.end  # the slot has not even started

    assert (await sweep.run()).purged == 1


async def test_a_suspended_booking_follows_its_closure_and_an_open_one_waits_for_the_maximum(
    sweep: RetentionSweep,
    bookings: BookingService,
    closures: ClosureService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    ended = await _book(bookings)
    receipt = await book_confirmed(
        bookings, group_slug="demo-twee", start=SLOT_0900, contact=ADA, locale="nl"
    )
    open_ended = receipt.booking.id or 0
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, SLOT_0930, NOTICES))
    await closures.close_group(SUPER, "demo-twee", ClosureInput(None, None, NOTICES))
    for planned in (ended, open_ended):
        assert uow.state.bookings[planned].status is BookingStatus.SUSPENDED
    clock.advance(SLOT_0930 - NOW + RETENTION_AFTER_CLOSURE + DAY + timedelta(hours=2))

    result = await sweep.run()

    assert result.purged == 1  # the open-ended one is not yet at its maximum
    assert not _holds_data(uow, ended)
    assert _holds_data(uow, open_ended)


async def test_an_open_closure_cannot_hold_data_past_the_maximum(
    sweep: RetentionSweep,
    bookings: BookingService,
    closures: ClosureService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    booking_id = await _book(bookings)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))
    maximum = SLOT_0930 + DEFAULT_CLOSURE_RETENTION.max_retention
    clock.advance(maximum - NOW + timedelta(minutes=30))  # within the grace
    assert (await sweep.run()).overdue == 0
    assert _holds_data(uow, booking_id)

    clock.advance(timedelta(hours=1))
    result = await sweep.run()

    assert (result.overdue, result.purged) == (1, 1)
    assert not _holds_data(uow, booking_id)


async def test_the_sweep_uses_the_maximum_of_the_bookings_organisation(
    bookings: BookingService,
    closures: ClosureService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    booking_id = await _book(bookings)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))
    clock.advance(SLOT_0930 - NOW + 31 * DAY)
    shorter = RetentionSweep(uow, clock, lambda _org: ClosureRetention.from_days(30, 3))

    assert (await RetentionSweep(uow, clock).run(dry_run=True)).overdue == 0
    assert (await shorter.run()).purged == 1
    assert not _holds_data(uow, booking_id)


def _notices(uow: InMemoryUnitOfWork) -> list[tuple[str | None, str]]:
    return [(e.group, e.detail) for e in uow.state.audit if e.action == CLOSURE_NOTICE]


async def test_the_administrators_are_told_before_the_maximum_with_counts_and_a_date(
    sweep: RetentionSweep,
    bookings: BookingService,
    closures: ClosureService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    await _book(bookings)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))
    maximum = SLOT_0930 + DEFAULT_CLOSURE_RETENTION.max_retention
    clock.advance(maximum - timedelta(days=15) - NOW)
    assert (await sweep.run()).notices == 0
    assert _notices(uow) == []

    clock.advance(timedelta(days=1))  # 14 days left: the notice period
    result = await sweep.run()

    assert result.notices == 1
    [(group, detail)] = _notices(uow)
    assert group == "demo"
    [closure_id] = uow.state.closures
    assert detail == f"closure:{closure_id},bookings:1,delete_by:{maximum.date().isoformat()}"
    assert "@" not in detail  # counts and a date, no participant data


async def test_the_notice_is_given_once_a_day_and_not_for_a_dry_run(
    sweep: RetentionSweep,
    bookings: BookingService,
    closures: ClosureService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    await _book(bookings)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))
    clock.advance(SLOT_0930 + DEFAULT_CLOSURE_RETENTION.max_retention - NOW - timedelta(days=10))

    await sweep.run(dry_run=True)
    assert _notices(uow) == []
    await sweep.run()
    await sweep.run()
    assert len(_notices(uow)) == 1
    clock.advance(DAY)
    await sweep.run()

    assert len(_notices(uow)) == 2


async def test_a_closure_that_ends_before_the_maximum_needs_no_notice(
    sweep: RetentionSweep,
    bookings: BookingService,
    closures: ClosureService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    await _book(bookings)
    ends = SLOT_0930 + timedelta(days=60)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, ends, NOTICES))
    clock.advance(ends + RETENTION_AFTER_CLOSURE - NOW - timedelta(days=5))  # before the maximum

    assert (await sweep.run()).notices == 0
    assert _notices(uow) == []
