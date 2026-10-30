"""BookingService and the participant's confirmation by email link (ADR 0056 step 2).

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; the group has one place on Monday
09:00 Amsterdam. With the default policy a new booking must be confirmed within 24 hours,
with one reminder halfway.
"""

from datetime import timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.confirmation import ConfirmPolicy
from app.domain.errors import DomainError
from app.domain.job_plan import JobType, PlannedJob
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import (
    NOW,
    SLOT_0930,
    TEST_TOKEN_KEY,
    book,
    book_pending,
    make_group,
    window,
)

CONFIRM_BY = NOW + timedelta(hours=24)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.groups[1] = make_group(id=1, slug="aangifte")
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    state.reminder_rules[1] = [ReminderRule(ChannelKind.EMAIL, minutes_before=24 * 60)]
    return InMemoryUnitOfWork(state)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def service(uow: InMemoryUnitOfWork, clock: FixedClock) -> BookingService:
    return BookingService(uow, clock, TEST_TOKEN_KEY)


def _types(uow: InMemoryUnitOfWork, booking_id: int | None) -> list[JobType]:
    return [job.type for job in uow.state.jobs[booking_id or 0]]


async def test_a_new_booking_awaits_confirmation_and_only_asks_for_it(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book_pending(service)

    booking = receipt.booking
    assert booking.status is BookingStatus.PENDING_CONFIRMATION
    assert booking.confirm_by == CONFIRM_BY
    assert booking.confirmed_at is None
    # No meeting, reminder of the appointment or task before the address is verified.
    assert uow.state.jobs[booking.id or 0] == [
        PlannedJob(JobType.SEND_CONFIRM_REQUEST, NOW, None, ChannelKind.EMAIL),
        PlannedJob(
            JobType.SEND_CONFIRM_REMINDER, NOW + timedelta(hours=12), None, ChannelKind.EMAIL
        ),
        PlannedJob(JobType.EXPIRE_UNCONFIRMED, CONFIRM_BY, None),
        PlannedJob(JobType.PURGE_PII, CONFIRM_BY + timedelta(hours=24), None),
    ]


async def test_the_organisations_policy_sets_the_deadline(uow: InMemoryUnitOfWork) -> None:
    def policy(organisation_id: int) -> ConfirmPolicy:
        assert organisation_id == 1
        return ConfirmPolicy.from_settings(
            window_hours=2, cutoff_hours=2, reminders=0, min_minutes=15
        )

    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, confirm_policy=policy)

    receipt = await book_pending(service)

    assert receipt.booking.confirm_by == NOW + timedelta(hours=2)
    assert JobType.SEND_CONFIRM_REMINDER not in _types(uow, receipt.booking.id)


async def test_an_unconfirmed_booking_holds_its_place(service: BookingService) -> None:
    # Abuse case "slot exhaustion with unconfirmed bookings": the place is held, but only
    # until confirm_by (see the lapse below).
    await book_pending(service)

    with pytest.raises(DomainError, match="slot_full"):
        await book_pending(service)


async def test_confirming_plans_the_appointment(
    service: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    receipt = await book_pending(service)
    clock.advance(timedelta(hours=3))

    confirmed = await service.confirm(receipt.management_token)

    assert confirmed.status is BookingStatus.CONFIRMED
    assert confirmed.confirmed_at == NOW + timedelta(hours=3)
    assert uow.state.bookings[confirmed.id or 0] == confirmed
    types = _types(uow, confirmed.id)
    assert types[:2] == [JobType.CREATE_VIDEO_ROOM, JobType.SEND_CONFIRMATION]
    assert JobType.EXPIRE_UNCONFIRMED not in types
    assert JobType.DISPATCH_WXCC_TASK in types


async def test_a_second_click_changes_nothing(
    service: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    receipt = await book_pending(service)
    first = await service.confirm(receipt.management_token)
    jobs = list(uow.state.jobs[first.id or 0])
    clock.advance(timedelta(hours=1))

    again = await service.confirm(receipt.management_token)

    assert again == first
    assert uow.state.jobs[first.id or 0] == jobs


async def test_a_confirmation_after_the_deadline_lapses_the_booking(
    service: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    receipt = await book_pending(service)
    clock.advance(timedelta(hours=24))

    lapsed = await service.confirm(receipt.management_token)

    assert lapsed.status is BookingStatus.CANCELLED
    assert lapsed.lapsed_unconfirmed
    assert uow.state.bookings[lapsed.id or 0].status is BookingStatus.CANCELLED
    assert _types(uow, lapsed.id)[-1] is JobType.SEND_UNCONFIRMED_LAPSED
    await book_pending(service)  # the place is free again


async def test_a_moved_booking_keeps_its_confirmation(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    old = await book(service)

    new = await service.reschedule(old.management_token, SLOT_0930)

    assert new.booking.status is BookingStatus.CONFIRMED
    assert new.booking.confirmed_at == old.booking.confirmed_at == NOW
    assert new.booking.confirm_by is None
    assert JobType.SEND_CHANGE in _types(uow, new.booking.id)


async def test_an_unconfirmed_booking_cannot_be_moved_by_the_participant(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    receipt = await book_pending(service)

    with pytest.raises(DomainError, match="booking_not_active"):
        await service.reschedule(receipt.management_token, SLOT_0930)


async def test_an_unconfirmed_booking_can_be_cancelled_but_not_changed(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book_pending(service)

    details = await service.find(receipt.management_token)
    assert not details.changeable
    assert details.cancellable
    cancelled = await service.cancel(receipt.management_token)

    assert cancelled.status is BookingStatus.CANCELLED
    assert not cancelled.lapsed_unconfirmed


async def test_a_planner_move_of_a_pending_booking_keeps_it_pending(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    receipt = await book_pending(service)
    group = uow.state.groups[1]

    async with uow.locked() as tx:
        moved = await service.relocate(tx, receipt.booking, group, SLOT_0930)

    assert moved.booking.status is BookingStatus.PENDING_CONFIRMATION
    assert moved.booking.confirm_by == CONFIRM_BY
    assert _types(uow, moved.booking.id)[0] is JobType.SEND_CONFIRM_REQUEST
