"""Archived groups are erased once they have waited and nothing refers to them (ADR 0084)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.audit import SYSTEM_ACTOR, Outcome, Source
from app.domain.booking import Booking, BookingStatus, Slot
from app.domain.group_erase import ERASED, GroupEraser
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import two_organisations
from tests.support.domain import NOW, SLOT_0900

AFTER = timedelta(days=30)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def _eraser(uow: InMemoryUnitOfWork, clock: FixedClock) -> GroupEraser:
    return GroupEraser(uow, clock, lambda: AFTER)


async def test_a_group_is_erased_after_the_delay_with_a_system_event(
    uow: InMemoryUnitOfWork,
) -> None:
    uow.state.groups[1] = replace(uow.state.groups[1], archived_at=NOW)
    clock = FixedClock(NOW + AFTER - timedelta(minutes=1))

    assert await _eraser(uow, clock).run() == 0
    assert 1 in uow.state.groups

    clock.advance(timedelta(minutes=1))
    assert await _eraser(uow, clock).run() == 1
    assert 1 not in uow.state.groups
    assert 2 in uow.state.groups  # in use, never touched
    event = uow.state.audit[-1]
    assert (event.actor, event.action, event.outcome, event.source) == (
        SYSTEM_ACTOR,
        ERASED,
        Outcome.SUCCESS,
        Source.SYSTEM,
    )
    assert (event.organisation, event.group) == ("demo", "demo")


def _booking_on(uow: InMemoryUnitOfWork) -> None:
    end = SLOT_0900 + timedelta(minutes=30)
    uow.state.slots[50] = Slot(50, 1, SLOT_0900, end, 2)
    uow.state.bookings[60] = Booking(
        id=60,
        group_id=1,
        slot_id=50,
        start=SLOT_0900,
        end=end,
        status=BookingStatus.CANCELLED,
        contact=None,
        locale="nl",
        token_hash="h",
        created_at=NOW,
    )


async def test_a_group_with_a_booking_waits(uow: InMemoryUnitOfWork) -> None:
    uow.state.groups[1] = replace(uow.state.groups[1], archived_at=NOW)
    _booking_on(uow)
    clock = FixedClock(NOW + AFTER)

    assert await _eraser(uow, clock).run() == 0
    assert 1 in uow.state.groups

    del uow.state.bookings[60]  # the purge removed the booking, the slot stays behind
    assert await _eraser(uow, clock).run() == 1
    assert 1 not in uow.state.groups


async def test_slots_nothing_books_do_not_keep_an_archived_group(
    uow: InMemoryUnitOfWork,
) -> None:
    # Release audit v0.4.0 P2 (#505): purge and rollup delete bookings, never their slots.
    uow.state.groups[1] = replace(uow.state.groups[1], archived_at=NOW)
    uow.state.slots[50] = Slot(50, 1, SLOT_0900, SLOT_0900 + timedelta(minutes=30), 2)
    uow.state.slots[51] = Slot(51, 2, SLOT_0900, SLOT_0900 + timedelta(minutes=30), 2)

    assert await _eraser(uow, FixedClock(NOW + AFTER)).run() == 1
    assert 1 not in uow.state.groups
    assert 50 not in uow.state.slots  # erased with the group
    assert 51 in uow.state.slots  # another group's slot is never touched


async def test_a_group_in_use_is_never_erased(uow: InMemoryUnitOfWork) -> None:
    assert await _eraser(uow, FixedClock(NOW + timedelta(days=400))).run() == 0
    assert set(uow.state.groups) == {1, 2}
