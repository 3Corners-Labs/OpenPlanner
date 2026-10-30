import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import JobType
from app.domain.scheduling import (
    DispatchKind,
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
)
from app.domain.services import BookingService, ContactInput
from app.domain.tokens import derive_token, hash_token
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import (
    ADA,
    MONDAY,
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    TUESDAY_0900,
    book,
    book_confirmed,
    make_group,
    window,
)


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
    return BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)


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
    assert booking.end == SLOT_0930
    assert booking.created_at == NOW
    assert booking.token_hash == hash_token(receipt.management_token)
    assert receipt.management_token == derive_token(TEST_TOKEN_KEY, booking.token_salt)
    assert booking.contact is not None
    assert booking.contact.phone == "+31612345678"
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


async def test_bookings_on_an_old_grid_count_against_overlapping_slots(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await book(service)  # 09:00–09:30, capacity 1
    # The window moves to 08:45: the grid becomes 08:45, 09:15 and both overlap 09:00.
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window("08:45", "10:00", 1))]

    assert await service.availability("aangifte", MONDAY, MONDAY) == []
    with pytest.raises(DomainError, match="slot_full"):
        await book(service, SLOT_0900 - timedelta(minutes=15))

    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window("08:45", "10:00", 2))]
    await book(service, SLOT_0900 - timedelta(minutes=15))  # 08:45: one agent left
    await book(service, SLOT_0900 + timedelta(minutes=15))  # 09:15: 08:45 has ended
    with pytest.raises(DomainError, match="slot_full"):
        await book(service, SLOT_0900 + timedelta(minutes=15))


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
            contact=ContactInput(first_name="Ada", last_name="Lovelace", email="not-an-email"),
            locale="nl",
        )

    assert excinfo.value.field == "email"
    assert uow.state.bookings == {}
    assert uow.state.slots == {}


async def test_group_phone_mode_is_applied(service: BookingService) -> None:
    with pytest.raises(DomainError, match="phone"):
        await service.book(
            group_slug="toeslagen",
            start=TUESDAY_0900,
            contact=ContactInput(first_name="Ada", last_name="Lovelace", email="ada@example.org"),
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

    assert (await service.find(receipt.management_token)).booking.id == receipt.booking.id
    cancelled = await service.cancel(receipt.management_token)

    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.cancelled_at == NOW
    assert [j.type for j in uow.state.jobs[cancelled.id or 0]] == [
        JobType.END_WXCC_TASK,
        JobType.DELETE_VIDEO_ROOM,
        JobType.DELETE_TRIAL_CALL,
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
    assert [j.type for j in uow.state.jobs[old.booking.id or 0]] == [
        JobType.END_WXCC_TASK,
        JobType.DELETE_VIDEO_ROOM,
        JobType.DELETE_TRIAL_CALL,
        JobType.PURGE_PII,
    ]
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

    with pytest.raises(DomainError, match="slot_unavailable"):
        await service.reschedule(old.management_token, TUESDAY_0900)

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


async def test_booking_window_boundaries_are_inclusive(uow: InMemoryUnitOfWork) -> None:
    earliest = BookingService(uow, FixedClock(SLOT_0900 - timedelta(minutes=60)), TEST_TOKEN_KEY)
    latest = BookingService(uow, FixedClock(SLOT_0930 - timedelta(days=60)), TEST_TOKEN_KEY)

    await book(earliest)
    await book(latest, SLOT_0930)


async def test_slot_day_is_taken_in_the_group_time_zone(uow: InMemoryUnitOfWork) -> None:
    # Open Tuesday 00:00-01:00 Amsterdam, which is Monday 23:00 UTC.
    uow.state.rules[1] = [ScheduleRule(weekday=1, window=window("00:00", "01:00", 1))]
    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)

    receipt = await book(service, datetime(2026, 1, 5, 23, 0, tzinfo=UTC))

    assert receipt.booking.start == datetime(2026, 1, 5, 23, 0, tzinfo=UTC)


async def test_availability_applies_overrides_in_range(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.overrides[1] = [ScheduleOverride(day=MONDAY, window=window("09:30", "10:00", 1))]

    slots = await service.availability("aangifte", MONDAY, MONDAY)

    assert [s.start for s in slots] == [SLOT_0930]


async def test_consents_are_stored_and_kept_on_reschedule(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.reminder_rules[1] = [ReminderRule(ChannelKind.WHATSAPP, minutes_before=60)]
    consents = frozenset({ChannelKind.WHATSAPP})
    old = await book_confirmed(
        service,
        group_slug="aangifte",
        start=SLOT_0900,
        contact=ADA,
        locale="nl",
        consents=consents,
    )

    new = await service.reschedule(old.management_token, SLOT_0930)

    assert old.booking.consents == consents
    assert new.booking.consents == consents
    assert ChannelKind.WHATSAPP in [j.channel for j in uow.state.jobs[new.booking.id or 0]]
    assert uow.state.bookings[old.booking.id or 0].cancelled_at == NOW


async def test_purged_booking_cannot_be_rescheduled(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    from dataclasses import replace

    old = await book(service)
    booking_id = old.booking.id or 0
    uow.state.bookings[booking_id] = replace(uow.state.bookings[booking_id], contact=None)

    with pytest.raises(DomainError, match="booking_not_active"):
        await service.reschedule(old.management_token, SLOT_0930)


@pytest.mark.parametrize(
    "now",
    [
        SLOT_0900 - timedelta(minutes=60),  # the change lead
        SLOT_0900,  # the meeting starts
        SLOT_0900 + timedelta(hours=2),  # long after
    ],
)
async def test_booking_cannot_be_moved_from_the_change_lead(
    uow: InMemoryUnitOfWork, now: datetime
) -> None:
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    late = BookingService(uow, FixedClock(now), TEST_TOKEN_KEY)

    with pytest.raises(DomainError) as excinfo:
        await late.reschedule(receipt.management_token, SLOT_0930)

    assert excinfo.value.code is ErrorCode.BOOKING_LOCKED
    details = await late.find(receipt.management_token)
    assert details.booking.status is BookingStatus.CONFIRMED
    assert details.changeable is False
    jobs = uow.state.jobs[receipt.booking.id or 0]
    assert JobType.DISPATCH_WXCC_TASK in [j.type for j in jobs]


@pytest.mark.parametrize(
    "now",
    [
        SLOT_0900,  # the meeting starts
        SLOT_0900 + timedelta(hours=2),  # long after
    ],
)
async def test_booking_cannot_be_cancelled_from_the_start(
    uow: InMemoryUnitOfWork, now: datetime
) -> None:
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    late = BookingService(uow, FixedClock(now), TEST_TOKEN_KEY)

    with pytest.raises(DomainError) as excinfo:
        await late.cancel(receipt.management_token)

    assert excinfo.value.code is ErrorCode.BOOKING_LOCKED
    assert (await late.find(receipt.management_token)).cancellable is False


@pytest.mark.parametrize("minutes_before", [59, 30, 6, 1])
async def test_booking_can_be_cancelled_after_the_change_lead_and_dispatch(
    uow: InMemoryUnitOfWork, minutes_before: int
) -> None:
    """ADR 0069: no cancel-lock before the start; the task is ended by the job (ADR 0021)."""
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    late = BookingService(
        uow, FixedClock(SLOT_0900 - timedelta(minutes=minutes_before)), TEST_TOKEN_KEY
    )

    details = await late.find(receipt.management_token)
    cancelled = await late.cancel(receipt.management_token)

    assert details.changeable is False
    assert details.cancellable is True
    assert cancelled.status is BookingStatus.CANCELLED
    jobs = [j.type for j in uow.state.jobs[receipt.booking.id or 0]]
    assert JobType.END_WXCC_TASK in jobs
    assert JobType.DISPATCH_WXCC_TASK not in jobs


async def test_booking_can_be_moved_until_just_before_the_change_lead(
    uow: InMemoryUnitOfWork,
) -> None:
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    just_before = BookingService(
        uow, FixedClock(SLOT_0900 - timedelta(minutes=60, seconds=1)), TEST_TOKEN_KEY
    )

    moved = await just_before.reschedule(receipt.management_token, SLOT_0930)

    assert moved.booking.start == SLOT_0930


def test_dispatch_moment_is_the_start_minus_the_lead() -> None:
    from app.domain.job_plan import dispatch_moment

    assert dispatch_moment(SLOT_0900, timedelta(minutes=15)) == SLOT_0900 - timedelta(minutes=15)


@pytest.mark.parametrize("kind", list(DispatchKind))
async def test_both_kinds_can_still_cancel_a_day_ahead_and_after_the_lead(
    uow: InMemoryUnitOfWork, kind: DispatchKind
) -> None:
    """The product owner's case: a Monday booking is not locked from Friday on."""
    uow.state.groups[1] = make_group(id=1, slug="aangifte", dispatch_kind=kind)
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    sunday = BookingService(uow, FixedClock(SLOT_0900 - timedelta(days=1)), TEST_TOKEN_KEY)

    details = await sunday.find(receipt.management_token)

    assert details.changeable
    assert details.cancellable


async def test_dispatch_job_follows_the_groups_dispatch_lead(uow: InMemoryUnitOfWork) -> None:
    uow.state.groups[1] = make_group(
        id=1,
        slug="aangifte",
        dispatch_lead_minutes=15,
        min_notice_minutes=60,
        citizen_change_lead_minutes=15,
    )
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    at_lead = BookingService(uow, FixedClock(SLOT_0900 - timedelta(minutes=15)), TEST_TOKEN_KEY)

    with pytest.raises(DomainError, match="booking_locked"):
        await at_lead.reschedule(receipt.management_token, SLOT_0930)
    dispatch = [
        j for j in uow.state.jobs[receipt.booking.id or 0] if j.type is JobType.DISPATCH_WXCC_TASK
    ]
    assert dispatch[0].run_at == SLOT_0900 - timedelta(minutes=15)


async def test_find_returns_group_and_changeability(service: BookingService) -> None:
    receipt = await book(service)

    details = await service.find(receipt.management_token)

    assert details.group.slug == "aangifte"
    assert details.changeable is True
    assert details.change_deadline == SLOT_0900 - timedelta(minutes=60)
    assert details.cancellable is True


async def test_find_of_a_cancelled_booking_is_not_changeable(service: BookingService) -> None:
    receipt = await book(service)
    await service.cancel(receipt.management_token)

    assert (await service.find(receipt.management_token)).changeable is False


async def test_get_group(service: BookingService) -> None:
    assert (await service.get_group("aangifte")).id == 1
    with pytest.raises(DomainError, match="group_not_found"):
        await service.get_group("nope")


def test_token_key_must_be_long_enough(uow: InMemoryUnitOfWork) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        BookingService(uow, FixedClock(NOW), b"short")


async def test_own_queue_team_gets_an_invite_and_its_withdrawal(uow: InMemoryUnitOfWork) -> None:
    uow.state.groups[1] = make_group(
        id=1,
        slug="aangifte",
        dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
        calendar_invite_address="team-aangifte@example.org",
    )
    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)
    receipt = await book(service)
    booking_id = receipt.booking.id or 0
    planned = [j.type for j in uow.state.jobs[booking_id]]

    await service.cancel(receipt.management_token)

    assert JobType.SEND_TEAM_INVITE in planned
    assert JobType.SEND_TEAM_CANCEL in [j.type for j in uow.state.jobs[booking_id]]


# --- bookings per email address (ADR 0066) ------------------------------------------------

BEA = ContactInput(first_name="Bea", last_name="de Vries", email="bea@example.org")
"""A second citizen, for tests that need two people in one slot."""


def _limit_service(uow: InMemoryUnitOfWork, limit: int) -> BookingService:
    """Return a service for group 1 with capacity 3 and ``limit`` bookings per address."""
    uow.state.groups[1] = make_group(id=1, slug="aangifte", max_bookings_per_email=limit)
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window("09:00", "12:00", 3))]
    return BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)


async def test_the_same_address_in_the_same_slot_is_refused(uow: InMemoryUnitOfWork) -> None:
    service = _limit_service(uow, 10)
    await book(service)
    shouting = ContactInput(first_name="Ada", last_name="L.", email="ADA@Example.org")

    with pytest.raises(DomainError) as excinfo:
        await service.book(group_slug="aangifte", start=SLOT_0900, contact=shouting, locale="nl")

    assert excinfo.value.code is ErrorCode.BOOKING_LIMIT_REACHED
    await service.book(group_slug="aangifte", start=SLOT_0900, contact=BEA, locale="nl")


async def test_an_address_may_hold_up_to_the_group_maximum(uow: InMemoryUnitOfWork) -> None:
    service = _limit_service(uow, 2)
    first = await book(service)  # on behalf of someone else: a second place is allowed
    await book(service, SLOT_0930)

    with pytest.raises(DomainError, match="booking_limit_reached"):
        await book(service, SLOT_0930 + timedelta(minutes=30))

    await service.cancel(first.management_token)  # a cancelled booking frees the address
    await book(service, SLOT_0930 + timedelta(minutes=30))


async def test_the_limit_counts_only_this_group_and_the_future(
    uow: InMemoryUnitOfWork,
) -> None:
    service = _limit_service(uow, 1)
    await book(service)
    uow.state.groups[2] = make_group(id=2, slug="toeslagen", max_bookings_per_email=1)
    await service.book(group_slug="toeslagen", start=TUESDAY_0900, contact=ADA, locale="nl")

    with pytest.raises(DomainError, match="booking_limit_reached"):
        await book(service, SLOT_0930)

    later = BookingService(uow, FixedClock(SLOT_0930 + timedelta(minutes=1)), TEST_TOKEN_KEY)
    uow.state.groups[1] = make_group(
        id=1,
        slug="aangifte",
        min_notice_minutes=0,
        dispatch_lead_minutes=0,
        max_bookings_per_email=1,
    )
    await book(later, SLOT_0930 + timedelta(minutes=30))  # the 09:00 booking has ended


async def test_rescheduling_does_not_count_the_booking_it_moves(
    uow: InMemoryUnitOfWork,
) -> None:
    service = _limit_service(uow, 1)
    old = await book(service)

    moved = await service.reschedule(old.management_token, SLOT_0930)

    assert moved.booking.start == SLOT_0930


async def test_a_full_slot_is_reported_before_the_address_limit(
    service: BookingService,
) -> None:
    await book(service)  # capacity 1: the slot is full for everyone, Ada included

    with pytest.raises(DomainError, match="slot_full"):
        await book(service)
