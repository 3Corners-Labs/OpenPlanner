"""Booking an appointment on a counted capacity pool (ADR 0122 step 5), on the in-memory fake.

The gate ``ROLLBACK_SAFE`` is opened per test (``open_gate``); with it shut nothing here is
reachable (``test_pool_booking_gate.py``). Monday 5 January 2026, pool block 09:00–12:00
Amsterdam (08:00–11:00 UTC) with 2 units unless a test says otherwise; the service's own
staffing is Monday 09:00–10:00 with 3 people, so its own times differ from the pool's.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.closure import Closure
from app.domain.errors import DomainError, ErrorCode
from app.domain.holidays import Holiday, HolidayCalendar
from app.domain.job_plan import JobType
from app.domain.pool_availability import PoolTarget
from app.domain.pool_booking import PoolBookings
from app.domain.scheduling import Offer, Organisation, ScheduleOverride, ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import (
    MONDAY,
    NOW,
    TEST_TOKEN_KEY,
    make_group,
    participant,
    window,
)
from tests.support.pool_flows import (
    EVERYTHING,
    PoolFlow,
    add_counted_pool,
    add_pool_flow,
    block,
    open_gate,
)

AT_0900 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
AT_0920 = datetime(2026, 1, 5, 8, 20, tzinfo=UTC)
AT_0940 = datetime(2026, 1, 5, 8, 40, tzinfo=UTC)
NOTICES = {"nl": "Gesloten.", "en": "Closed."}


@pytest.fixture(autouse=True)
def gate(monkeypatch: pytest.MonkeyPatch) -> None:
    open_gate(monkeypatch)


def _state() -> State:
    state = State()
    state.organisations = {1: Organisation(1, "Acme", "acme"), 2: Organisation(2, "Beta", "beta")}
    state.organisation_slugs = {1: "acme", 2: "beta"}
    state.groups[1] = make_group(id=1, slug="balie")
    state.groups[2] = make_group(id=2, slug="kassa")
    state.groups[3] = make_group(id=3, organisation_id=2, slug="loket")
    for group_id in state.groups:
        state.rules[group_id] = [ScheduleRule(weekday=0, window=window(capacity=3))]
    state.next_id = 100
    return state


async def _world(
    blocks: list[ScheduleRule] | None = None, duration: int = 20
) -> tuple[InMemoryUnitOfWork, PoolFlow]:
    uow = InMemoryUnitOfWork(_state())
    async with uow.locked() as tx:
        for group_id in (1, 2, 3):
            await tx.ensure_default_flows(group_id)
    pool_id = await add_counted_pool(uow, 1, blocks or [block()])
    return uow, await add_pool_flow(uow, 1, pool_id, duration=duration)


def _service(uow: InMemoryUnitOfWork, now: datetime = NOW, buffer: int = 0) -> BookingService:
    return BookingService(uow, FixedClock(now), TEST_TOKEN_KEY, treatment_buffer=buffer)


async def _book(
    service: BookingService,
    start: datetime,
    number: int = 1,
    slug: str = "balie",
    flow: str = "advies",
):  # type: ignore[no-untyped-def]
    return await service.book(
        group_slug=slug, start=start, contact=participant(number), locale="nl", flow=flow
    )


async def _starts(service: BookingService, flow: str = "advies", slug: str = "balie") -> list:  # type: ignore[type-arg]
    return [
        (s.start, s.remaining) for s in await service.availability(slug, MONDAY, MONDAY, flow=flow)
    ]


def _code(error: pytest.ExceptionInfo[DomainError]) -> ErrorCode:
    return error.value.code


# --- availability: the pool's grid, minus what admission refuses ------------------------------


async def test_the_pool_flow_offers_the_pools_grid_not_the_services_times() -> None:
    uow, _ = await _world()

    pool_times = await _starts(_service(uow))
    own_times = await _starts(_service(uow), flow="appointment")

    assert [t for t, _ in pool_times] == [AT_0900 + timedelta(minutes=20 * n) for n in range(9)]
    assert {r for _, r in pool_times} == {2}
    assert [t for t, _ in own_times] == [AT_0900, AT_0900 + timedelta(minutes=30)]


async def test_the_step_is_length_plus_wrap_up_and_the_slot_ends_without_it() -> None:
    uow, _ = await _world()

    slots = await _service(uow, buffer=10).availability("balie", MONDAY, MONDAY, flow="advies")

    assert [s.start for s in slots] == [AT_0900 + timedelta(minutes=30 * n) for n in range(6)]
    assert all(s.end - s.start == timedelta(minutes=20) for s in slots)


@pytest.mark.parametrize(
    ("offer", "offered"), [(Offer.SCHEDULED, True), (Offer.BOTH, True), (Offer.DIRECT, False)]
)
async def test_a_block_offers_appointments_only_when_its_offer_says_so_p7(
    offer: Offer, offered: bool
) -> None:
    uow, _ = await _world([block(offer=offer)])

    assert bool(await _starts(_service(uow))) is offered


async def test_a_zero_unit_block_ends_a_run_and_a_long_appointment_does_not_span_it() -> None:
    blocks = [
        block(closes="10:00"),
        block(opens="10:00", closes="10:30", units=0),
        block(opens="10:30", closes="12:00"),
    ]
    uow, _ = await _world(blocks, duration=60)

    starts = [t for t, _ in await _starts(_service(uow))]

    local = [t.astimezone(make_group().tz).strftime("%H:%M") for t in starts]
    assert local == ["09:00", "10:30"]


async def test_a_pool_without_time_zone_or_staffing_offers_nothing() -> None:
    uow = InMemoryUnitOfWork(_state())
    pool_id = await add_counted_pool(uow, 1, [block()], timezone=None)
    await add_pool_flow(uow, 1, pool_id)

    assert await _starts(_service(uow)) == []
    with pytest.raises(DomainError) as error:
        await _book(_service(uow), AT_0900)
    assert _code(error) is ErrorCode.SLOT_UNAVAILABLE


async def test_a_holiday_of_the_organisation_closes_the_pool_unless_it_has_its_own_exception() -> (
    None
):
    uow, flow = await _world()
    async with uow.locked() as tx:
        calendar = await tx.add_holiday_calendar(HolidayCalendar(0, "feest", "Feestdagen"))
        await tx.add_holiday(Holiday(calendar.id, MONDAY, "Feestdag"))
        await tx.save_organisation_holiday_calendars(1, frozenset({calendar.id}))

    assert await _starts(_service(uow)) == []

    async with uow.locked() as tx:
        opened = ScheduleOverride(MONDAY, window("10:00", "11:00", 1))
        await tx.add_pool_override(EVERYTHING, flow.pool_id, opened)
    assert [r for _, r in await _starts(_service(uow))] == [1, 1, 1]


# --- booking: the admission sweep inside the lock ---------------------------------------------


async def test_a_booking_creates_a_pool_slot_and_records_its_flow() -> None:
    uow, flow = await _world()

    receipt = await _book(_service(uow, buffer=10), AT_0900)

    slot = uow.state.slots[receipt.booking.slot_id]
    assert (slot.capacity_pool_id, slot.start, slot.end) == (flow.pool_id, AT_0900, AT_0920)
    assert slot.occupied_until == AT_0900 + timedelta(minutes=30)
    assert slot.capacity_snapshot == 2  # a record only: admission reads live staffing
    assert receipt.booking.end == AT_0920
    assert receipt.booking.flow is not None
    assert receipt.booking.flow.flow_id == flow.flow_id
    assert receipt.booking.status is BookingStatus.PENDING_CONFIRMATION


async def test_bookings_of_one_start_share_the_slot_and_the_third_is_full() -> None:
    uow, _ = await _world()
    service = _service(uow)

    first = await _book(service, AT_0900, 1)
    second = await _book(service, AT_0900, 2)
    with pytest.raises(DomainError) as error:
        await _book(service, AT_0900, 3)

    assert _code(error) is ErrorCode.SLOT_FULL
    assert first.booking.slot_id == second.booking.slot_id
    assert (AT_0900, 2) not in await _starts(service)
    assert AT_0900 not in [t for t, _ in await _starts(service)]


@pytest.mark.parametrize(
    ("start", "code"),
    [
        (AT_0900 + timedelta(minutes=10), ErrorCode.SLOT_UNAVAILABLE),  # not on the grid
        (datetime(2026, 1, 5, 11, 0, tzinfo=UTC), ErrorCode.SLOT_UNAVAILABLE),  # after 12:00
        (datetime(2026, 1, 6, 8, 0, tzinfo=UTC), ErrorCode.SLOT_UNAVAILABLE),  # Tuesday
        (NOW + timedelta(minutes=30), ErrorCode.OUTSIDE_BOOKING_WINDOW),
        (NOW + timedelta(days=90), ErrorCode.OUTSIDE_BOOKING_WINDOW),
    ],
)
async def test_a_start_the_pool_does_not_offer_is_refused_and_nothing_is_stored(
    start: datetime, code: ErrorCode
) -> None:
    uow, _ = await _world()

    with pytest.raises(DomainError) as error:
        await _book(_service(uow), start)

    assert _code(error) is code
    assert not uow.state.bookings
    assert not [s for s in uow.state.slots.values() if s.capacity_pool_id]


async def test_a_closure_of_the_service_refuses_intake_on_the_pool() -> None:
    uow, _ = await _world()
    async with uow.locked() as tx:
        await tx.add_closure(Closure(None, 1, NOW, None, NOTICES, NOW))

    assert await _starts(_service(uow)) == []
    with pytest.raises(DomainError) as error:
        await _book(_service(uow), AT_0900)
    assert _code(error) is ErrorCode.GROUP_CLOSED


async def test_a11_two_services_share_one_pool_and_never_overbook_it() -> None:
    uow, flow = await _world()
    await add_pool_flow(uow, 2, flow.pool_id, code="intake", duration=40)
    service = _service(uow)

    await _book(service, AT_0900, 1)  # 09:00–09:20 on balie's flow
    await _book(service, AT_0900, 2, slug="kassa", flow="intake")  # 09:00–09:40

    kassa = await _starts(service, flow="intake", slug="kassa")
    assert AT_0900 not in [t for t, _ in kassa]
    assert AT_0900 not in [t for t, _ in await _starts(service)]
    assert (AT_0920, 1) in await _starts(service)  # kassa's one still holds a unit to 09:40
    with pytest.raises(DomainError) as error:
        await _book(service, AT_0900, 3)
    assert _code(error) is ErrorCode.SLOT_FULL


async def test_a14_different_lengths_meet_only_through_their_intervals() -> None:
    uow, flow = await _world()
    await add_pool_flow(uow, 2, flow.pool_id, code="lang", duration=40)
    service = _service(uow)
    await _book(service, AT_0920, 1)  # 09:20–09:40
    await _book(service, AT_0920, 2)  # both units 09:20–09:40

    with pytest.raises(DomainError) as error:
        await _book(service, AT_0900, 3, slug="kassa", flow="lang")  # 09:00–09:40
    assert _code(error) is ErrorCode.SLOT_FULL
    assert (AT_0900, 2) in await _starts(service)  # a 20-minute one at 09:00 still fits


async def test_a12_a_full_pool_leaves_another_pool_alone() -> None:
    uow, flow = await _world([block(units=1)])
    other = await add_counted_pool(uow, 1, [block(units=1)], name="Ander")
    await add_pool_flow(uow, 2, other, code="ander")
    service = _service(uow)
    await _book(service, AT_0900, 1)

    assert AT_0900 not in [t for t, _ in await _starts(service)]
    assert (AT_0900, 1) in await _starts(service, flow="ander", slug="kassa")


async def test_a_pool_of_another_organisation_offers_nothing_a05() -> None:
    """A flow that names a pool of another organisation (a tampered row) books nothing there."""
    uow, flow = await _world()
    foreign = await add_counted_pool(uow, 2, [block()], name="Vreemd")
    for key, stored in list(uow.state.flow_revisions.items()):
        if stored.capacity_pool_id == flow.pool_id:
            uow.state.flow_revisions[key] = replace(stored, capacity_pool_id=foreign)
    tampered = uow.state.flows[flow.flow_id]
    assert tampered.current is not None
    uow.state.flows[flow.flow_id] = replace(
        tampered, current=replace(tampered.current, capacity_pool_id=foreign)
    )

    assert await _starts(_service(uow)) == []
    with pytest.raises(DomainError) as error:
        await _book(_service(uow), AT_0900)
    assert _code(error) is ErrorCode.SLOT_UNAVAILABLE


async def test_p3_a_staffing_drop_stops_new_bookings_and_keeps_the_existing_ones() -> None:
    uow, flow = await _world()
    service = _service(uow)
    for n in (1, 2):
        await service.confirm((await _book(service, AT_0900, n)).management_token)
    async with uow.locked() as tx:
        staffing = await tx.get_pool_staffing(EVERYTHING, flow.pool_id)
        rule = staffing.rules[0]
        await tx.update_pool_rule(EVERYTHING, flow.pool_id, rule.id, block(units=1))

    with pytest.raises(DomainError) as error:
        await _book(service, AT_0900, 3)  # 2 held over 1 staffed: short, nothing new
    assert _code(error) is ErrorCode.SLOT_FULL
    held = [b for b in uow.state.bookings.values() if b.status is BookingStatus.CONFIRMED]
    assert len(held) == 2  # nothing rewritten, moved or cancelled
    times = await _starts(service)
    assert AT_0900 not in [t for t, _ in times]
    assert (AT_0920, 1) in times  # live staffing: one unit from 09:20 on


async def test_p4_a_suspended_appointment_keeps_its_unit_in_the_pool() -> None:
    uow, flow = await _world([block(units=1)])
    await add_pool_flow(uow, 2, flow.pool_id, code="intake")
    service = _service(uow)
    receipt = await _book(service, AT_0900, 1)
    confirmed = await service.confirm(receipt.management_token)
    async with uow.locked() as tx:
        closure = await tx.add_closure(Closure(None, 1, NOW, None, NOTICES, NOW))
        found = await tx.find_confirmed_bookings(1, NOW, None, include_pool=True)
        assert [b.id for b in found] == [confirmed.id]
        await tx.save_booking(confirmed.suspend(closure.id))

    kassa = await _starts(service, flow="intake", slug="kassa")
    assert AT_0900 not in [t for t, _ in kassa]
    with pytest.raises(DomainError) as error:
        await _book(service, AT_0900, 2, slug="kassa", flow="intake")
    assert _code(error) is ErrorCode.SLOT_FULL


async def test_cancelling_frees_the_place_at_once() -> None:
    uow, _ = await _world([block(units=1)])
    service = _service(uow)
    receipt = await _book(service, AT_0900)
    assert AT_0900 not in [t for t, _ in await _starts(service)]

    await service.cancel(receipt.management_token)

    assert (AT_0900, 1) in await _starts(service)


# --- moving: the booking stays on its pool and is admitted again -----------------------------


async def test_a_move_keeps_the_flow_and_the_pool_and_frees_the_old_unit() -> None:
    uow, flow = await _world([block(units=1)])
    service = _service(uow)
    first = await _book(service, AT_0900)
    await service.confirm(first.management_token)

    moved = await service.reschedule(first.management_token, AT_0940)

    slot = uow.state.slots[moved.booking.slot_id]
    assert slot.capacity_pool_id == flow.pool_id
    assert moved.booking.flow == first.booking.flow
    assert (AT_0900, 1) in await _starts(service)
    assert AT_0940 not in [t for t, _ in await _starts(service)]


async def test_a_move_into_a_full_time_or_the_services_own_time_is_refused_untouched() -> None:
    uow, _ = await _world([block(units=1)])
    service = _service(uow)
    mine = await _book(service, AT_0900, 1)
    await service.confirm(mine.management_token)
    await _book(service, AT_0920, 2)

    for target, code in (
        (AT_0920, ErrorCode.SLOT_FULL),
        (AT_0900 + timedelta(minutes=30), ErrorCode.SLOT_UNAVAILABLE),
    ):
        with pytest.raises(DomainError) as error:
            await service.reschedule(mine.management_token, target)
        assert _code(error) is code
    stored = uow.state.bookings[mine.booking.id or 0]
    assert stored.status is BookingStatus.CONFIRMED
    assert stored.start == AT_0900


async def test_a_booking_on_a_pool_moves_on_its_pool_even_with_the_gate_shut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rollback: an image with the gate shut meets a pool booking and keeps it on the pool."""
    uow, flow = await _world()
    service = _service(uow)
    first = await _book(service, AT_0900)
    await service.confirm(first.management_token)
    monkeypatch.setattr("app.domain.service_flows.ROLLBACK_SAFE", False)

    moved = await service.reschedule(first.management_token, AT_0940)

    assert uow.state.slots[moved.booking.slot_id].capacity_pool_id == flow.pool_id
    picker_times = await service.move_times(moved.booking, MONDAY, MONDAY)
    assert AT_0900 in [s.start for s in picker_times]


# --- retention: the wrap-up never extends personal data ---------------------------------------


async def test_the_purge_follows_the_appointment_end_not_the_wrap_up() -> None:
    uow, _ = await _world()
    service = _service(uow, buffer=60)
    receipt = await _book(service, AT_0900)

    confirmed = await service.confirm(receipt.management_token)

    purge = [j for j in uow.state.jobs[confirmed.id or 0] if j.type is JobType.PURGE_PII]
    assert [j.run_at for j in purge] == [AT_0920 + timedelta(hours=24)]
    slot = uow.state.slots[confirmed.slot_id]
    assert slot.occupied_until == AT_0900 + timedelta(minutes=80)


# --- daylight saving time ---------------------------------------------------------------------


async def test_spring_forward_a_90_minute_appointment_across_the_missing_hour() -> None:
    sunday = date(2026, 3, 29)
    uow, _ = await _world([block(weekday=6, opens="01:00", closes="05:00")], duration=90)
    service = _service(uow, now=datetime(2026, 3, 20, 8, 0, tzinfo=UTC))

    slots = await service.availability("balie", sunday, sunday, flow="advies")

    assert [s.start for s in slots] == [datetime(2026, 3, 29, 0, 0, tzinfo=UTC)]
    receipt = await _book(service, slots[0].start)
    slot = uow.state.slots[receipt.booking.slot_id]
    assert slot.occupied_until == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


async def test_fall_back_the_repeated_hour_is_offered_once() -> None:
    sunday = date(2026, 10, 25)
    uow, _ = await _world([block(weekday=6, opens="01:00", closes="04:00", units=1)], duration=60)
    service = _service(uow, now=datetime(2026, 10, 20, 8, 0, tzinfo=UTC))

    starts = [s.start for s in await service.availability("balie", sunday, sunday, flow="advies")]

    assert starts == [
        datetime(2026, 10, 24, 23, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 2, 0, tzinfo=UTC),
    ]
    for n, start in enumerate(starts):
        await _book(service, start, n)
    assert await service.availability("balie", sunday, sunday, flow="advies") == []


# --- one source per page: the calendar's times, loaded once (review findings 1 and 2) --------


async def test_a_calendar_reads_a_pool_once_and_shows_what_availability_shows() -> None:
    uow, _ = await _world()
    service = _service(uow)
    expected = await service.availability("balie", MONDAY, MONDAY, flow="advies")

    times = await service.free_times("balie", flow="advies")
    await _book(service, AT_0900, 1)  # after loading: the calendar of this page keeps its scene

    assert await times.between(MONDAY, MONDAY) == expected
    assert (await service.free_times("balie", flow="advies")) is not times


async def test_a_calendar_of_the_services_own_times_reads_them_as_availability_does() -> None:
    uow, _ = await _world()
    service = _service(uow)

    times = await service.free_times("balie", flow="appointment")

    own = await service.availability("balie", MONDAY, MONDAY, flow="appointment")
    assert await times.between(MONDAY, MONDAY) == own


async def test_a_move_calendar_of_a_pool_booking_shows_the_pools_times() -> None:
    uow, _ = await _world()
    service = _service(uow)
    receipt = await _book(service, AT_0900, 1)

    times = await service.free_times("balie", booking=receipt.booking)

    assert [s.start for s in await times.between(MONDAY, MONDAY)] == [
        s.start for s in await service.availability("balie", MONDAY, MONDAY, flow="advies")
    ]


async def test_a_booking_whose_slot_cannot_be_read_is_never_put_on_own_staffing() -> None:
    uow, _ = await _world()
    service = _service(uow)
    receipt = await _book(service, AT_0900, 1)
    del uow.state.slots[receipt.booking.slot_id]

    with pytest.raises(DomainError) as error:
        await service.move_times(receipt.booking, MONDAY, MONDAY)
    assert _code(error) is ErrorCode.SLOT_UNAVAILABLE


async def test_move_times_refuse_an_archived_service() -> None:
    uow, _ = await _world()
    service = _service(uow)
    receipt = await _book(service, AT_0900, 1)
    uow.state.groups[1] = replace(uow.state.groups[1], archived_at=NOW)

    with pytest.raises(DomainError) as error:
        await service.move_times(receipt.booking, MONDAY, MONDAY)
    assert _code(error) is ErrorCode.GROUP_NOT_FOUND


async def test_a_day_beyond_the_horizon_offers_nothing_on_the_pool() -> None:
    uow, _ = await _world()
    far = MONDAY + timedelta(days=91)

    assert await _service(uow).availability("balie", far, far, flow="advies") == []


async def test_the_pool_reservation_refuses_a_naive_start_and_an_archived_service() -> None:
    uow, flow = await _world()
    pools = PoolBookings(FixedClock(NOW), treatment_buffer=0)
    target = PoolTarget(flow.pool_id, 20)
    async with uow.locked() as tx:
        with pytest.raises(ValueError, match="aware"):
            await pools.reserve(tx, make_group(id=1), target, AT_0900.replace(tzinfo=None))
        archived = make_group(id=1, archived_at=NOW)
        with pytest.raises(DomainError) as error:
            await pools.reserve(tx, archived, target, AT_0900)
    assert _code(error) is ErrorCode.GROUP_NOT_FOUND


async def test_a25_cancelling_or_moving_releases_the_unit_once_and_plans_no_meeting() -> None:
    """A25: a move frees the old unit once, a cancel the new one; no meeting or task is left."""
    uow, _ = await _world([block(units=1)])
    service = _service(uow)
    first = await _book(service, AT_0900)
    await service.confirm(first.management_token)

    moved = await service.reschedule(first.management_token, AT_0940)
    await service.cancel(moved.management_token)
    with pytest.raises(DomainError):  # a second cancel releases nothing again
        await service.cancel(moved.management_token)

    starts = await _starts(service)
    assert (AT_0900, 1) in starts
    assert (AT_0940, 1) in starts
    late = {JobType.CREATE_VIDEO_ROOM, JobType.DISPATCH_CONTACT_CENTER_TASK}
    pending = [j for jobs in uow.state.jobs.values() for j in jobs if j.type in late]
    assert pending == []
