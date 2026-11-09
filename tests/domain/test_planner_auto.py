"""Automatic proposals of the rebooking assistant (ADR 0057 point 6), through the service."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.audit import SYSTEM_ACTOR, Outcome, Source
from app.domain.auto_rebooking import AUTO_PROPOSAL_EVENT, REVIEW_WAITING_EVENT
from app.domain.booking import BookingStatus
from app.domain.planner_service import PlannerService
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import SLOT_0900, SLOT_0930, book_another, set_agents

DELAY = timedelta(minutes=15)


def _auto(uow: InMemoryUnitOfWork, *, maximum: int = 10, on: bool = True) -> None:
    uow.state.groups[1] = replace(
        uow.state.groups[1],
        reschedule_auto_send=on,
        reschedule_auto_delay_minutes=15,
        reschedule_auto_max_bookings=maximum,
    )


async def _two_booked_then_short(
    bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> list[int]:
    ids = []
    for _ in range(2):
        ids.append((await book_another(bookings, SLOT_0900, "demo")).booking.id or 0)
        clock.advance(timedelta(minutes=1))
    set_agents(uow, 1)  # the slot keeps its snapshot of 2; the staffing is now 1
    return ids


def _statuses(uow: InMemoryUnitOfWork, ids: list[int]) -> list[BookingStatus]:
    return [uow.state.bookings[i].status for i in ids]


async def test_a_shortfall_is_acted_on_only_after_it_stayed_the_same_for_the_delay(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _auto(uow)
    ids = await _two_booked_then_short(bookings, uow, clock)

    assert await planner.run_automatic() == 0  # first seen: the clock starts
    clock.advance(DELAY - timedelta(minutes=1))
    assert await planner.run_automatic() == 0
    assert _statuses(uow, ids) == [BookingStatus.CONFIRMED, BookingStatus.CONFIRMED]
    clock.advance(timedelta(minutes=1))
    assert await planner.run_automatic() == 1

    assert _statuses(uow, ids) == [BookingStatus.CONFIRMED, BookingStatus.AWAITING_REBOOK]
    (event,) = [e for e in uow.state.audit if e.action == AUTO_PROPOSAL_EVENT]
    assert (event.actor, event.source, event.outcome) == (
        SYSTEM_ACTOR,
        Source.SYSTEM,
        Outcome.SUCCESS,
    )
    assert event.detail.endswith(",reason:staffing")
    assert not [e for e in uow.state.audit if e.action == "bookings.manage"]  # no planner acted
    assert event.booking_id == ids[1]  # the last-made booking moves, as in the review


async def test_a_typo_in_the_staffing_that_is_undone_starts_the_delay_again(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _auto(uow)
    ids = await _two_booked_then_short(bookings, uow, clock)
    await planner.run_automatic()  # short: waiting
    clock.advance(timedelta(minutes=10))
    set_agents(uow, 2)  # the typo is undone
    await planner.run_automatic()
    assert uow.state.watches == {}  # nothing is short, nothing is remembered
    clock.advance(timedelta(minutes=2))
    set_agents(uow, 1)  # short again
    await planner.run_automatic()

    clock.advance(timedelta(minutes=13))  # 25 minutes after the first scan, 13 after the new one
    assert await planner.run_automatic() == 0
    clock.advance(timedelta(minutes=2))
    assert await planner.run_automatic() == 1
    assert _statuses(uow, ids) == [BookingStatus.CONFIRMED, BookingStatus.AWAITING_REBOOK]


async def test_a_group_that_did_not_switch_it_on_is_left_alone(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    ids = await _two_booked_then_short(bookings, uow, clock)

    await planner.run_automatic()
    clock.advance(timedelta(hours=1))
    assert await planner.run_automatic() == 0

    assert _statuses(uow, ids) == [BookingStatus.CONFIRMED, BookingStatus.CONFIRMED]
    assert uow.state.watches == {}


async def test_above_the_ceiling_nothing_is_sent_and_the_planners_are_told_once(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _auto(uow, maximum=1)
    ids = []
    for start in (SLOT_0900, SLOT_0930):
        for _ in range(2):
            ids.append((await book_another(bookings, start, "demo")).booking.id or 0)
            clock.advance(timedelta(minutes=1))
    set_agents(uow, 1)  # both slots short by one: two bookings affected, ceiling one
    await planner.run_automatic()
    clock.advance(DELAY)

    assert await planner.run_automatic() == 0
    clock.advance(timedelta(minutes=5))
    assert await planner.run_automatic() == 0

    assert set(_statuses(uow, ids)) == {BookingStatus.CONFIRMED}
    (event,) = [e for e in uow.state.audit if e.action == REVIEW_WAITING_EVENT]
    assert (event.actor, event.source, event.outcome) == (
        SYSTEM_ACTOR,
        Source.SYSTEM,
        Outcome.FAILURE,
    )
    assert (event.reason, event.detail, event.group) == (
        "too_many_bookings",
        "bookings:2,max:1",
        "demo",
    )


async def test_switching_it_off_forgets_what_was_seen_so_the_delay_runs_in_full_again(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _auto(uow)
    ids = await _two_booked_then_short(bookings, uow, clock)
    await planner.run_automatic()
    assert uow.state.watches  # waiting
    _auto(uow, on=False)
    await planner.run_automatic()
    assert uow.state.watches == {}
    clock.advance(timedelta(hours=3))  # long past the old first sighting
    _auto(uow)

    assert await planner.run_automatic() == 0  # the clock starts again
    clock.advance(DELAY)
    assert await planner.run_automatic() == 1
    assert uow.state.bookings[ids[1]].status is BookingStatus.AWAITING_REBOOK


async def test_one_failing_group_does_not_keep_the_others_from_their_proposals(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _auto(uow)
    ids = await _two_booked_then_short(bookings, uow, clock)
    uow.state.groups[2] = replace(uow.state.groups[2], reschedule_auto_send=True)
    original = planner._automatic  # noqa: SLF001

    async def broken_first(tx, summary):  # type: ignore[no-untyped-def]
        if summary.group.id == 2:
            raise RuntimeError("one group is broken")
        return await original(tx, summary)

    planner._automatic = broken_first  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(RuntimeError):
        await planner.run_automatic()  # first scan: group 1 starts waiting, group 2 fails
    clock.advance(DELAY)

    with pytest.raises(RuntimeError, match="one group is broken"):
        await planner.run_automatic()  # the failure is reported after the others went

    assert uow.state.bookings[ids[1]].status is BookingStatus.AWAITING_REBOOK
