"""The wait watch (ADR 0061 OV-7): one warning alert per crossing of the wait threshold."""

from datetime import timedelta

import pytest

from app.domain.alerts import QUEUE_WAIT_EVENT, QUEUE_WAIT_OVER
from app.domain.audit import Outcome, Source
from app.domain.calls import CallStatus
from app.domain.queue_wait import QueueWaitWatch
from app.domain.scheduling import DispatchKind, Organisation, ScheduleRule
from app.domain.services import BookingService
from app.domain.system_log import SystemLog
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import (
    ADA,
    NOW,
    SLOT_0900,
    TEST_TOKEN_KEY,
    book_confirmed,
    make_group,
    window,
)

THRESHOLD = timedelta(minutes=8)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    groups = {"demo": DispatchKind.OPENPLANNER_QUEUE, "wxcc": DispatchKind.CONTACT_CENTER}
    state = State(
        organisation_slugs={1: "demo"}, organisations={1: Organisation(1, "Demo", "demo")}
    )
    for group_id, (slug, kind) in enumerate(groups.items(), start=1):
        state.groups[group_id] = make_group(
            id=group_id, organisation_id=1, slug=slug, dispatch_kind=kind
        )
        state.rules[group_id] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    state.next_id = 100
    return InMemoryUnitOfWork(state)


async def _waiting(uow: InMemoryUnitOfWork, clock: FixedClock) -> int:
    """Book the 09:00 slot and put its participant in the lobby; return the booking id."""
    bookings = BookingService(uow, clock, TEST_TOKEN_KEY)
    receipt = await book_confirmed(
        bookings, group_slug="demo", start=SLOT_0900, contact=ADA, locale="nl"
    )
    booking_id = receipt.booking.id or 0
    clock.advance(SLOT_0900 - clock.now())
    uow.state.call_status[booking_id] = CallStatus(
        booking_id, participants=1, updated_at=clock.now()
    )
    return booking_id


def _watch(
    uow: InMemoryUnitOfWork, clock: FixedClock, reported: set[str] | None = None
) -> QueueWaitWatch:
    async def open_groups() -> set[str]:
        return set(reported or ())

    return QueueWaitWatch(
        uow, clock, SystemLog(uow, clock), lambda organisation: THRESHOLD, open_groups
    )


def _waits(uow: InMemoryUnitOfWork) -> list[tuple[Outcome, str | None, str, str]]:
    return [
        (e.outcome, e.group, e.reason, e.detail)
        for e in uow.state.audit
        if e.action == QUEUE_WAIT_EVENT
    ]


async def test_one_alert_per_crossing_and_one_end_when_back_under(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _waiting(uow, clock)
    watch = _watch(uow, clock)

    assert await watch.check() == []
    clock.advance(timedelta(minutes=9))
    assert await watch.check() == ["demo"]
    clock.advance(timedelta(minutes=5))
    assert await watch.check() == []  # still over: no second alert
    uow.state.call_status[booking_id] = CallStatus(
        booking_id, participants=2, updated_at=clock.now()
    )  # picked up: nobody waits
    assert await watch.check() == ["demo"]

    assert _waits(uow) == [
        (Outcome.FAILURE, "demo", QUEUE_WAIT_OVER, "minutes:9"),
        (Outcome.SUCCESS, "demo", "", "minutes:0"),
    ]
    assert all(e.source is Source.SYSTEM for e in uow.state.audit)
    assert {e.organisation for e in uow.state.audit} == {"demo"}


async def test_a_restart_closes_an_alert_whose_queue_emptied_meanwhile(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    watch = _watch(uow, clock, reported={"demo"})

    assert await watch.check() == ["demo"]
    assert _waits(uow) == [(Outcome.SUCCESS, "demo", "", "minutes:0")]


async def test_a_restart_does_not_repeat_an_alert_that_is_still_open(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await _waiting(uow, clock)
    clock.advance(timedelta(minutes=9))
    watch = _watch(uow, clock, reported={"demo"})

    assert await watch.check() == []
    assert _waits(uow) == []


async def test_a_queue_switched_off_is_forgotten_without_an_event(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    watch = _watch(uow, clock, reported={"gone"})

    await watch.check()

    assert _waits(uow) == []
