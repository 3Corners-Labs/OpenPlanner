import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import JobType
from app.domain.scheduling import PhoneMode, ScheduleOverride, ScheduleRule, TimeWindow
from app.domain.services import BookingService, ContactInput
from app.domain.tokens import hash_token
from tests.domain.test_scheduling import make_group
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State

NOW = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
MONDAY = date(2026, 1, 5)
SLOT_0900 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # 09:00 Amsterdam
SLOT_0930 = datetime(2026, 1, 5, 8, 30, tzinfo=UTC)
ADA = ContactInput(name="Ada Lovelace", email="ada@example.org", phone="0612345678")


def window(capacity: int) -> TimeWindow:
    return TimeWindow(time(9), time(10), capacity)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.groups[1] = make_group(id=1, slug="aangifte")
    state.groups[2] = make_group(id=2, slug="toeslagen", phone_mode=PhoneMode.REQUIRED)
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    state.rules[2] = [ScheduleRule(weekday=1, window=window(capacity=1))]  # Tuesdays
    state.reminder_rules[1] = [ReminderRule(ChannelKind.EMAIL, minutes_before=24 * 60)]
    return InMemoryUnitOfWork(state)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> BookingService:
    return BookingService(uow, FixedClock(NOW))


async def book(service: BookingService, start: datetime = SLOT_0900, slug: str = "aangifte"):  # type: ignore[no-untyped-def]
    return await service.book(group_slug=slug, start=start, contact=ADA, locale="nl")


async def test_availability_lists_free_slots(service: BookingService) -> None:
    await book(service)

    slots = await service.availability("aangifte", MONDAY, MONDAY)

    assert [s.start for s in slots] == [SLOT_0930]


async def test_availability_errors(service: BookingService) -> None:
    with pytest.raises(DomainError, match="group_not_found"):
        await service.availability("unknown", MONDAY, MONDAY)
    with pytest.raises(DomainError, match="invalid_range"):
        await service.availability("aangifte", MONDAY, MONDAY - timedelta(days=1))


async def test_book_creates_slot_booking_and_jobs(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(service)

    booking = receipt.booking
    assert booking.status is BookingStatus.CONFIRMED
    assert booking.start == SLOT_0900
    assert booking.token_hash == hash_token(receipt.management_token)
    assert booking.contact is not None
    assert booking.contact.phone == "0612345678"
    slot = uow.state.slots[booking.slot_id]
    assert slot.capacity_snapshot == 1
    types = [job.type for job in uow.state.jobs[booking.id or 0]]
    assert types == [
        JobType.CREATE_VIDEO_ROOM,
        JobType.SEND_CONFIRMATION,
        JobType.SEND_REMINDER,
        JobType.DISPATCH_WXCC_TASK,
        JobType.PURGE_PII,
    ]


async def test_full_slot_is_refused(service: BookingService) -> None:
    await book(service)

    with pytest.raises(DomainError) as excinfo:
        await book(service)

    assert excinfo.value.code is ErrorCode.SLOT_FULL


async def test_capacity_snapshot_survives_schedule_changes(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await book(service)
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=5))]

    with pytest.raises(DomainError, match="slot_full"):
        await book(service)
    await book(service, SLOT_0930)  # new slots use the new capacity


async def test_closing_a_day_stops_new_bookings_in_existing_slots(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    await book(service)
    uow.state.overrides[1] = [ScheduleOverride(day=MONDAY, window=None)]

    with pytest.raises(DomainError, match="slot_unavailable"):
        await book(service)


@pytest.mark.parametrize(
    ("start", "code"),
    [
        (datetime(2026, 1, 5, 8, 7, tzinfo=UTC), ErrorCode.SLOT_UNAVAILABLE),
        (datetime(2026, 1, 6, 8, 0, tzinfo=UTC), ErrorCode.SLOT_UNAVAILABLE),
        (NOW + timedelta(minutes=30), ErrorCode.OUTSIDE_BOOKING_WINDOW),
        (NOW + timedelta(days=90), ErrorCode.OUTSIDE_BOOKING_WINDOW),
    ],
)
async def test_unbookable_starts(service: BookingService, start: datetime, code: ErrorCode) -> None:
    with pytest.raises(DomainError) as excinfo:
        await book(service, start)

    assert excinfo.value.code is code


async def test_naive_start_is_a_programming_error(service: BookingService) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        await book(service, datetime(2026, 1, 5, 9, 0))  # noqa: DTZ001


async def test_invalid_contact_stores_nothing(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as excinfo:
        await service.book(
            group_slug="aangifte",
            start=SLOT_0900,
            contact=ContactInput(name="Ada", email="not-an-email"),
            locale="nl",
        )

    assert excinfo.value.field == "email"
    assert uow.state.bookings == {}
    assert uow.state.slots == {}


async def test_group_phone_mode_is_applied(service: BookingService) -> None:
    tuesday = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)

    with pytest.raises(DomainError, match="phone"):
        await service.book(
            group_slug="toeslagen",
            start=tuesday,
            contact=ContactInput(name="Ada", email="ada@example.org"),
            locale="en",
        )


async def test_unknown_group(service: BookingService) -> None:
    with pytest.raises(DomainError, match="group_not_found"):
        await book(service, slug="nope")


async def test_concurrent_bookings_for_the_last_place(service: BookingService) -> None:
    results = await asyncio.gather(book(service), book(service), return_exceptions=True)

    assert sum(not isinstance(r, BaseException) for r in results) == 1
    errors = [r for r in results if isinstance(r, DomainError)]
    assert [e.code for e in errors] == [ErrorCode.SLOT_FULL]


async def test_find_and_cancel(service: BookingService, uow: InMemoryUnitOfWork) -> None:
    receipt = await book(service)

    assert (await service.find(receipt.management_token)).id == receipt.booking.id
    cancelled = await service.cancel(receipt.management_token)

    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.cancelled_at == NOW
    assert [j.type for j in uow.state.jobs[cancelled.id or 0]] == [
        JobType.SEND_CANCELLATION,
        JobType.PURGE_PII,
    ]
    await book(service)  # the place is free again


async def test_cancel_twice(service: BookingService) -> None:
    receipt = await book(service)
    await service.cancel(receipt.management_token)

    with pytest.raises(DomainError, match="booking_not_active"):
        await service.cancel(receipt.management_token)


@pytest.mark.parametrize("token", ["", "guess", "x" * 43])
async def test_unknown_tokens_are_not_found(service: BookingService, token: str) -> None:
    await book(service)

    for operation in (service.find, service.cancel):
        with pytest.raises(DomainError, match="booking_not_found"):
            await operation(token)
    with pytest.raises(DomainError, match="booking_not_found"):
        await service.reschedule(token, SLOT_0930)


async def test_reschedule_moves_the_booking(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await book(service)

    new = await service.reschedule(old.management_token, SLOT_0930)

    assert new.booking.start == SLOT_0930
    assert new.booking.rescheduled_from_id == old.booking.id
    assert new.booking.contact == old.booking.contact
    assert new.booking.locale == "nl"
    assert new.management_token != old.management_token
    assert uow.state.bookings[old.booking.id or 0].status is BookingStatus.CANCELLED
    assert [j.type for j in uow.state.jobs[old.booking.id or 0]] == [JobType.PURGE_PII]
    assert JobType.SEND_CHANGE in [j.type for j in uow.state.jobs[new.booking.id or 0]]
    await book(service)  # the old place is free again


async def test_reschedule_into_the_same_slot(service: BookingService) -> None:
    old = await book(service)

    with pytest.raises(DomainError, match="same_slot"):
        await service.reschedule(old.management_token, SLOT_0900)


async def test_reschedule_into_another_groups_slot_is_refused(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    # Abuse case: the Tuesday slot exists only for group "toeslagen".
    old = await book(service)
    tuesday = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)

    with pytest.raises(DomainError, match="slot_unavailable"):
        await service.reschedule(old.management_token, tuesday)

    assert uow.state.bookings[old.booking.id or 0].status is BookingStatus.CONFIRMED
    assert all(s.group_id == 1 for s in uow.state.slots.values())


async def test_failed_reschedule_rolls_back(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await book(service)
    await service.book(group_slug="aangifte", start=SLOT_0930, contact=ADA, locale="en")

    with pytest.raises(DomainError, match="slot_full"):
        await service.reschedule(old.management_token, SLOT_0930)

    assert uow.state.bookings[old.booking.id or 0].status is BookingStatus.CONFIRMED
    assert JobType.SEND_CONFIRMATION in [j.type for j in uow.state.jobs[old.booking.id or 0]]


async def test_reschedule_a_cancelled_booking(service: BookingService) -> None:
    old = await book(service)
    await service.cancel(old.management_token)

    with pytest.raises(DomainError, match="booking_not_active"):
        await service.reschedule(old.management_token, SLOT_0930)


async def test_reschedule_when_the_group_disappeared(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await book(service)
    del uow.state.groups[1]

    with pytest.raises(DomainError, match="group_not_found"):
        await service.reschedule(old.management_token, SLOT_0930)


async def test_receipt_repr_hides_the_token(service: BookingService) -> None:
    receipt = await book(service)

    assert receipt.management_token not in repr(receipt)
