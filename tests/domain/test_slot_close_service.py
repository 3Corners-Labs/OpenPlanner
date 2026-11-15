"""The worker's closing of ended slots writes each slot's facts once (ADR 0117 step 3)."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.domain.agenda import ShortfallAcceptance
from app.domain.booking import BookingKind, BookingStatus, Slot
from app.domain.calls import CallState, CallStatus
from app.domain.services import BookingService, ContactInput
from app.domain.slot_close import BATCH_SLOTS, CLOSE_DELAY
from app.domain.slot_close_service import SlotCloser
from app.domain.usage import FactKind, UsageFact, UsageOutcome
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import three_groups
from tests.support.domain import (
    ADA,
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    book_confirmed,
    participant,
)

INTERVAL = timedelta(minutes=15)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(three_groups())


@pytest.fixture
def closer(uow: InMemoryUnitOfWork, clock: FixedClock) -> SlotCloser:
    return SlotCloser(uow, clock, lambda: INTERVAL)


def _facts(uow: InMemoryUnitOfWork) -> list[UsageFact]:
    return [f for f in uow.state.usage_facts if isinstance(f, UsageFact)]


async def _booked(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    start: datetime = SLOT_0900,
    contact: ContactInput = ADA,
    **call: object,
) -> int:
    bookings = BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)
    receipt = await book_confirmed(
        bookings, group_slug="demo", start=start, contact=contact, locale="nl"
    )
    booking_id = receipt.booking.id or 0
    if call:
        uow.state.call_status[booking_id] = CallStatus(booking_id, **call)  # type: ignore[arg-type]
    return booking_id


async def test_the_first_pass_only_sets_the_watermark(
    uow: InMemoryUnitOfWork, clock: FixedClock, closer: SlotCloser
) -> None:
    await _booked(uow, clock)
    clock.advance(timedelta(days=10))

    result = await closer.run(clock.now())

    assert result.slots == 0
    assert uow.state.slots_closed_until == clock.now() - CLOSE_DELAY
    assert [f.kind for f in _facts(uow) if f.kind is FactKind.SLOT_CLOSED] == []


async def test_a_slot_is_closed_after_the_delay_and_only_once(
    uow: InMemoryUnitOfWork, clock: FixedClock, closer: SlotCloser
) -> None:
    await closer.run(clock.now())  # sets the watermark at the upgrade
    booking_id = await _booked(
        uow,
        clock,
        call=CallState.ENDED,
        host_joined=True,
        guest_joined_at=SLOT_0900,
        peak_participants=2,
    )
    slot_end = SLOT_0900 + timedelta(minutes=30)
    clock.advance(slot_end + CLOSE_DELAY - timedelta(minutes=1) - clock.now())
    assert (await closer.run(clock.now())).slots == 0  # not yet

    clock.advance(timedelta(minutes=2))
    first = await closer.run(clock.now())
    second = await closer.run(clock.now() + timedelta(hours=1))

    assert (first.slots, second.slots) == (1, 0)
    by_kind = {
        f.kind: f for f in _facts(uow) if f.kind in (FactKind.SLOT_CLOSED, FactKind.BOOKING_OUTCOME)
    }
    assert by_kind[FactKind.SLOT_CLOSED].booked == 1
    assert by_kind[FactKind.SLOT_CLOSED].capacity == 2
    assert by_kind[FactKind.BOOKING_OUTCOME].outcome is UsageOutcome.COMPLETED
    assert uow.state.bookings[booking_id].status is BookingStatus.CONFIRMED  # no new status


async def test_the_facts_and_the_watermark_stand_or_fall_together(
    uow: InMemoryUnitOfWork, clock: FixedClock, closer: SlotCloser, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fakes.persistence import InMemoryTransaction

    await closer.run(clock.now())
    await _booked(uow, clock)
    clock.advance(timedelta(days=5))

    async def broken(self: InMemoryTransaction, until: object) -> None:
        raise RuntimeError("down")

    watermark = uow.state.slots_closed_until
    monkeypatch.setattr(InMemoryTransaction, "set_slots_closed_until", broken)
    with pytest.raises(RuntimeError):
        await closer.run(clock.now())

    assert uow.state.slots_closed_until == watermark
    assert not [f for f in _facts(uow) if f.kind is FactKind.SLOT_CLOSED]


async def test_a_shortfall_is_written_open_or_accepted(
    uow: InMemoryUnitOfWork, clock: FixedClock, closer: SlotCloser
) -> None:
    await closer.run(clock.now())
    for number in (1, 2):
        await _booked(uow, clock, contact=participant(number))
    for number in (3, 4):
        await _booked(uow, clock, start=SLOT_0930, contact=participant(number))
    uow.state.rules[1][0] = replace(
        uow.state.rules[1][0], window=replace(uow.state.rules[1][0].window, capacity=1)
    )
    uow.state.acceptances[(1, SLOT_0930)] = ShortfallAcceptance(1, SLOT_0930, 1, 2, NOW, "p")
    clock.advance(timedelta(days=5))

    await closer.run(clock.now())

    shortfalls = [f for f in _facts(uow) if f.kind is FactKind.SLOT_SHORTFALL]
    assert [(f.reason_code, f.capacity, f.booked, f.bucket_hour) for f in shortfalls] == [
        ("open", 1, 2, SLOT_0900),
        ("accepted", 1, 2, SLOT_0900),  # keyed on the hour of the slot start
    ]


async def test_a_full_batch_stops_between_slots_and_the_next_pass_goes_on(
    uow: InMemoryUnitOfWork, clock: FixedClock, closer: SlotCloser
) -> None:
    await closer.run(clock.now())
    first = SLOT_0900
    for number in range(BATCH_SLOTS + 5):
        start = first + timedelta(hours=number)
        uow.state.slots[1000 + number] = Slot(
            1000 + number, 1, start, start + timedelta(minutes=30), 2, BookingKind.SCHEDULED
        )
    clock.advance(timedelta(days=60))

    one = await closer.run(clock.now())
    two = await closer.run(clock.now())

    assert (one.slots, one.more) == (BATCH_SLOTS, True)
    assert (two.slots, two.more) == (5, False)
    closed = [f for f in _facts(uow) if f.kind is FactKind.SLOT_CLOSED]
    assert len({f.bucket_hour for f in closed}) == BATCH_SLOTS + 5


async def test_due_follows_the_interval(
    uow: InMemoryUnitOfWork, clock: FixedClock, closer: SlotCloser
) -> None:
    assert await closer.run_if_due() is not None
    assert await closer.run_if_due() is None
    clock.advance(INTERVAL)
    assert await closer.run_if_due() is not None
