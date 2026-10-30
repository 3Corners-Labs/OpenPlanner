"""Direct calls (ADR 0019): only in a block that offers them (ADR 0071), in its own slot."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.booking import BookingKind
from app.domain.calls import CallStatus, TaskState
from app.domain.direct_queue import DirectAvailability
from app.domain.errors import DomainError
from app.domain.job_plan import JobType
from app.domain.scheduling import Offer, ScheduleOverride, ScheduleRule, open_for_direct
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import ADA, TEST_TOKEN_KEY, make_group, window

MONDAY_0915 = datetime(2026, 1, 5, 8, 15, tzinfo=UTC)  # 09:15 Amsterdam
GROUP = make_group(id=1, slug="aangifte")
RULES = [ScheduleRule(weekday=0, window=window("09:00", "10:00", 1, Offer.BOTH))]
SLOTS_ONLY = [ScheduleRule(weekday=0, window=window("09:00", "10:00", 1))]
"""The same block offering appointments only: no direct call (ADR 0071)."""


def test_open_follows_windows_and_exceptions() -> None:
    assert open_for_direct(GROUP, RULES, [], MONDAY_0915)
    assert not open_for_direct(GROUP, RULES, [], MONDAY_0915 + timedelta(hours=1))  # 10:15
    assert not open_for_direct(GROUP, RULES, [], MONDAY_0915 + timedelta(days=1))  # Tuesday
    closed = [ScheduleOverride(MONDAY_0915.date(), None)]
    assert not open_for_direct(GROUP, RULES, closed, MONDAY_0915)


def test_closed_during_a_block_without_agents() -> None:
    rules = [
        ScheduleRule(weekday=0, window=window("09:00", "12:00", 3, Offer.BOTH)),
        ScheduleRule(weekday=0, window=window("12:00", "12:30", 0, Offer.BOTH)),
        ScheduleRule(weekday=0, window=window("12:30", "17:00", 3, Offer.BOTH)),
    ]
    at = MONDAY_0915

    assert open_for_direct(GROUP, rules, [], at + timedelta(hours=2, minutes=44))  # 11:59
    assert not open_for_direct(GROUP, rules, [], at + timedelta(hours=2, minutes=45))  # 12:00
    assert not open_for_direct(GROUP, rules, [], at + timedelta(hours=3, minutes=14))  # 12:29
    assert open_for_direct(GROUP, rules, [], at + timedelta(hours=3, minutes=15))  # 12:30


def test_only_a_block_that_offers_direct_calls_opens_them() -> None:
    """ADR 0071: the offer is per block; a direct-only block opens them as well."""
    direct_only = [ScheduleRule(weekday=0, window=window("09:00", "10:00", 1, Offer.DIRECT))]
    exception = [ScheduleOverride(MONDAY_0915.date(), window("09:00", "10:00", 1, Offer.BOTH))]

    assert not open_for_direct(GROUP, SLOTS_ONLY, [], MONDAY_0915)
    assert open_for_direct(GROUP, direct_only, [], MONDAY_0915)
    assert open_for_direct(GROUP, SLOTS_ONLY, exception, MONDAY_0915)  # the exception wins


def _service(
    group: object = GROUP, now: datetime = MONDAY_0915, rules: list[ScheduleRule] = RULES
) -> tuple[BookingService, InMemoryUnitOfWork]:
    state = State()
    state.groups[1] = group  # type: ignore[assignment]
    state.rules[1] = list(rules)
    uow = InMemoryUnitOfWork(state)
    return BookingService(uow, FixedClock(now), TEST_TOKEN_KEY), uow


async def test_direct_call_starts_now_in_its_own_slot() -> None:
    service, uow = _service()

    receipt = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    second = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")

    booking = receipt.booking
    assert booking.kind is BookingKind.DIRECT
    assert booking.start == MONDAY_0915
    assert booking.end == MONDAY_0915 + timedelta(minutes=30)
    assert second.booking.slot_id != booking.slot_id  # no shared capacity
    jobs = uow.state.jobs[booking.id or 0]
    assert [j.type for j in jobs] == [
        JobType.CREATE_VIDEO_ROOM,
        JobType.SEND_CONFIRMATION,
        JobType.DISPATCH_WXCC_TASK,
        JobType.PURGE_PII,
    ]
    assert all(j.run_at == MONDAY_0915 for j in jobs[:3])


async def test_direct_calls_do_not_take_scheduled_capacity() -> None:
    service, _ = _service()
    await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")

    slots = await service.availability("aangifte", MONDAY_0915.date(), MONDAY_0915.date())

    assert all(s.remaining == s.capacity for s in slots)


@pytest.mark.parametrize(
    ("rules", "now"),
    [
        (SLOTS_ONLY, MONDAY_0915),
        (RULES, MONDAY_0915 + timedelta(hours=2)),
    ],
)
async def test_direct_call_only_when_enabled_and_open(
    rules: list[ScheduleRule], now: datetime
) -> None:
    service, uow = _service(GROUP, now, rules)

    with pytest.raises(DomainError, match="direct_call_unavailable"):
        await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    assert uow.state.bookings == {}


async def test_availability_of_direct_calls_for_the_group_page() -> None:
    service, _ = _service()
    closed, _ = _service(now=MONDAY_0915 + timedelta(hours=2))
    off, _ = _service(rules=SLOTS_ONLY)

    assert await service.direct_call_availability("aangifte") is DirectAvailability.AVAILABLE
    assert await closed.direct_call_availability("aangifte") is DirectAvailability.UNAVAILABLE
    assert await off.direct_call_availability("aangifte") is DirectAvailability.UNAVAILABLE


CAPPED = replace(GROUP, direct_call_max_concurrent=1)


async def test_direct_call_is_refused_when_every_agent_is_taken() -> None:
    service, uow = _service(CAPPED)
    first = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")

    assert await service.direct_call_availability("aangifte") is DirectAvailability.FULL
    with pytest.raises(DomainError, match="direct_call_full"):
        await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    assert list(uow.state.bookings) == [first.booking.id]


async def test_an_ended_call_frees_its_place() -> None:
    service, uow = _service(CAPPED)
    first = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    booking_id = first.booking.id or 0
    uow.state.call_status[booking_id] = CallStatus(booking_id, task=TaskState.ENDED)

    assert await service.direct_call_availability("aangifte") is DirectAvailability.AVAILABLE
    await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")


async def test_a_cancelled_call_frees_its_place() -> None:
    service, uow = _service(CAPPED)
    first = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")

    uow.state.bookings[first.booking.id or 0] = first.booking.cancel(MONDAY_0915)

    assert await service.direct_call_availability("aangifte") is DirectAvailability.AVAILABLE


async def test_closed_wins_over_full() -> None:
    service, _ = _service(CAPPED)
    await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    later = BookingService(
        service._uow, FixedClock(MONDAY_0915 + timedelta(hours=2)), TEST_TOKEN_KEY
    )

    assert await later.direct_call_availability("aangifte") is DirectAvailability.UNAVAILABLE


async def test_queue_info_gives_the_place_in_line() -> None:
    service, _ = _service()
    first = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    second = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")

    info = await service.direct_queue(GROUP, second.booking.id)

    assert info.waiting == 2
    assert info.position == 2
    assert (await service.direct_queue(GROUP, first.booking.id)).position == 1
    assert info.typical_wait_minutes is None  # no pickups yet
