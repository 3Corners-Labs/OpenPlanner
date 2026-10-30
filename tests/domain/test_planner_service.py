import itertools
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.agenda_service import AgendaService
from app.domain.audit import SYSTEM_ACTOR, Outcome, Source
from app.domain.authorization import Principal
from app.domain.booking import BookingStatus, ChangeReason
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError
from app.domain.job_plan import JobType
from app.domain.planner_service import ALERT_ACTION, PlannerService
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, OTHER_TEAM
from tests.support.domain import (
    ADA,
    MONDAY,
    NOW,
    SLOT_0900,
    SLOT_0930,
    book_confirmed,
    citizen,
    window,
    with_team_invite,
)

SMS = frozenset({ChannelKind.SMS})


@pytest.fixture
def planner(uow: InMemoryUnitOfWork, clock: FixedClock, bookings: BookingService) -> PlannerService:
    return PlannerService(uow, clock, AdminService(uow, clock), bookings, alert_per_hour=50)


_CITIZENS = itertools.count(1)
"""Each booking of ``_book`` is another citizen, so the address limit (ADR 0059) stays out."""


async def _book(
    bookings: BookingService, start=SLOT_0900, slug: str = "demo", consents=frozenset()
) -> int:
    receipt = await book_confirmed(
        bookings,
        group_slug=slug,
        start=start,
        contact=citizen(next(_CITIZENS)),
        locale="nl",
        consents=consents,
    )
    return receipt.booking.id or 0


def _staff(uow: InMemoryUnitOfWork, agents: int) -> None:
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=agents))]


def _messages(uow: InMemoryUnitOfWork, booking_id: int) -> set[tuple[JobType, ChannelKind | None]]:
    return {(j.type, j.channel) for j in uow.state.jobs[booking_id] if j.channel is not None}


# --- move ---------------------------------------------------------------------------------


async def test_a_move_places_a_new_booking_and_tells_the_citizen(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old_id = await _book(bookings, consents=SMS)

    new = await planner.move(GROUP_ADMIN, "demo", old_id, SLOT_0930)

    assert new.start == SLOT_0930
    assert new.rescheduled_from_id == old_id
    assert uow.state.bookings[old_id].status is BookingStatus.CANCELLED
    assert {
        (JobType.SEND_CHANGE, ChannelKind.EMAIL),
        (JobType.SEND_CHANGE, ChannelKind.SMS),
    } <= _messages(uow, new.id or 0)
    assert _messages(uow, old_id) == set()  # no cancellation message for the old one
    (event,) = [e for e in uow.state.audit if e.action == "bookings.manage"]
    assert (event.actor, event.detail, event.booking_id) == ("g-1", "planner.move", old_id)


async def test_without_a_phone_number_only_an_email_goes_out(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book_confirmed(
        bookings, group_slug="demo", start=SLOT_0900, contact=replace(ADA, phone=None), locale="nl"
    )
    old_id = receipt.booking.id or 0

    new = await planner.move(GROUP_ADMIN, "demo", old_id, SLOT_0930)

    assert (JobType.SEND_CHANGE, ChannelKind.SMS) not in _messages(uow, new.id or 0)


async def test_a_move_that_causes_a_shortfall_needs_confirmation(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    first = await _book(bookings, SLOT_0900)
    await _book(bookings, SLOT_0930)
    _staff(uow, 1)  # the slots keep their snapshot of 2, the staffing is now 1

    with pytest.raises(DomainError, match="move_causes_shortfall"):
        await planner.move(GROUP_ADMIN, "demo", first, SLOT_0930)
    moved = await planner.move(GROUP_ADMIN, "demo", first, SLOT_0930, accept_shortfall=True)

    assert moved.start == SLOT_0930


async def test_a_booking_of_another_group_cannot_be_reached_through_this_group(
    planner: PlannerService, bookings: BookingService
) -> None:
    other = await _book(bookings, slug="andere-team")

    with pytest.raises(DomainError, match="booking_not_found"):
        await planner.move(GROUP_ADMIN, "demo", other, SLOT_0930)
    with pytest.raises(DomainError, match="booking_not_found"):
        await planner.cancel(GROUP_ADMIN, "demo", other, ChangeReason.STAFFING)


async def test_a_full_slot_stays_full(planner: PlannerService, bookings: BookingService) -> None:
    first = await _book(bookings, SLOT_0900)
    await _book(bookings, SLOT_0930)
    await _book(bookings, SLOT_0930)

    with pytest.raises(DomainError, match="slot_full"):
        await planner.move(GROUP_ADMIN, "demo", first, SLOT_0930, accept_shortfall=True)


# --- cancel -------------------------------------------------------------------------------


async def test_a_cancellation_carries_the_reason_to_the_citizen(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings, consents=SMS)

    await planner.cancel(GROUP_ADMIN, "demo", booking_id, ChangeReason.TECHNICAL)

    booking = uow.state.bookings[booking_id]
    assert (booking.status, booking.change_reason) == (
        BookingStatus.CANCELLED,
        ChangeReason.TECHNICAL,
    )
    assert _messages(uow, booking_id) == {
        (JobType.SEND_CANCELLATION, ChannelKind.EMAIL),
        (JobType.SEND_CANCELLATION, ChannelKind.SMS),
    }
    (event,) = [e for e in uow.state.audit if e.action == "bookings.manage"]
    assert event.detail == "planner.cancel,reason:technical"


async def test_a_cancellation_in_the_own_queue_withdraws_the_team_invite(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.groups[1] = with_team_invite(uow.state.groups[1])
    booking_id = await _book(bookings)

    await planner.cancel(GROUP_ADMIN, "demo", booking_id, ChangeReason.STAFFING)

    assert (JobType.SEND_TEAM_CANCEL, ChannelKind.EMAIL) in _messages(uow, booking_id)


# --- limits -------------------------------------------------------------------------------


async def test_shortly_before_the_start_a_planner_must_call_instead(
    planner: PlannerService, bookings: BookingService, clock: FixedClock
) -> None:
    booking_id = await _book(bookings)
    clock.advance(SLOT_0900 - timedelta(minutes=59) - NOW)  # planner lead 60 by default

    with pytest.raises(DomainError, match="planner_too_late"):
        await planner.cancel(GROUP_ADMIN, "demo", booking_id, ChangeReason.STAFFING)


async def test_a_dispatched_booking_is_not_changed(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings)
    uow.state.bookings[booking_id] = replace(uow.state.bookings[booking_id], wxcc_task_id="t-1")

    with pytest.raises(DomainError, match="planner_too_late"):
        await planner.move(GROUP_ADMIN, "demo", booking_id, SLOT_0930)


async def test_a_cancelled_booking_is_not_changed(
    planner: PlannerService, bookings: BookingService
) -> None:
    booking_id = await _book(bookings)
    await planner.cancel(GROUP_ADMIN, "demo", booking_id, ChangeReason.STAFFING)

    with pytest.raises(DomainError, match="booking_not_active"):
        await planner.booking(GROUP_ADMIN, "demo", booking_id)


@pytest.mark.parametrize("principal", [AUDITOR, OTHER_TEAM])
async def test_only_who_may_manage_the_groups_bookings_changes_them(
    planner: PlannerService, bookings: BookingService, principal: Principal
) -> None:
    booking_id = await _book(bookings)

    with pytest.raises(DomainError, match="not_permitted"):
        await planner.cancel(principal, "demo", booking_id, ChangeReason.STAFFING)
    with pytest.raises(DomainError, match="not_permitted"):
        await planner.accept(principal, "demo", SLOT_0900)


# --- accept -------------------------------------------------------------------------------


async def test_an_accepted_shortfall_leaves_the_summary_until_the_slot_changes(
    planner: PlannerService,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    agenda = AgendaService(uow, clock, AdminService(uow, clock), horizon_days=14)
    await _book(bookings)
    await _book(bookings)
    _staff(uow, 1)

    await planner.accept(GROUP_ADMIN, "demo", SLOT_0900)

    assert (await agenda.shortfalls(GROUP_ADMIN, "demo")).count == 0
    (day,) = await agenda.days(GROUP_ADMIN, "demo", MONDAY, 1)
    assert day.slots[0].accepted
    _staff(uow, 0)  # another agent falls ill: the shortfall is open again
    assert (await agenda.shortfalls(GROUP_ADMIN, "demo")).count == 1


async def test_a_slot_without_shortfall_cannot_be_accepted(
    planner: PlannerService, bookings: BookingService
) -> None:
    await _book(bookings)

    with pytest.raises(DomainError, match="slot_unavailable"):
        await planner.accept(GROUP_ADMIN, "demo", SLOT_0900)


# --- alert --------------------------------------------------------------------------------


async def test_many_changes_by_one_planner_raise_one_system_event(
    uow: InMemoryUnitOfWork, clock: FixedClock, bookings: BookingService
) -> None:
    planner = PlannerService(uow, clock, AdminService(uow, clock), bookings, alert_per_hour=1)
    ids = [await _book(bookings, start) for start in (SLOT_0900, SLOT_0900, SLOT_0930)]

    for booking_id in ids:
        await planner.cancel(GROUP_ADMIN, "demo", booking_id, ChangeReason.UNFORESEEN)

    alerts = [e for e in uow.state.audit if e.action == ALERT_ACTION]
    assert len(alerts) == 1
    (alert,) = alerts
    assert (alert.actor, alert.source, alert.outcome, alert.group) == (
        SYSTEM_ACTOR,
        Source.SYSTEM,
        Outcome.FAILURE,
        "demo",
    )
    assert alert.detail == "actor:g-1,changes:2"
    assert all(b.status is BookingStatus.CANCELLED for b in uow.state.bookings.values())


# --- ask to rebook ------------------------------------------------------------------------


async def test_asking_to_rebook_frees_the_place_and_tells_the_citizen(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings, consents=SMS)

    waiting = await planner.request_rebook(GROUP_ADMIN, "demo", booking_id, ChangeReason.STAFFING)

    assert waiting.status is BookingStatus.AWAITING_REBOOK
    assert waiting.rebook_by == SLOT_0900 + timedelta(days=14)
    assert (JobType.SEND_REBOOK_REQUEST, ChannelKind.SMS) in _messages(uow, booking_id)
    assert [b.id for b in await planner.waiting(GROUP_ADMIN, "demo")] == [booking_id]
    (event,) = [e for e in uow.state.audit if e.action == "bookings.manage"]
    assert (event.detail, event.booking_id) == ("planner.rebook,reason:staffing", booking_id)


async def test_asking_to_rebook_in_the_own_queue_withdraws_the_team_invite(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.groups[1] = with_team_invite(uow.state.groups[1])
    booking_id = await _book(bookings)

    await planner.request_rebook(GROUP_ADMIN, "demo", booking_id, ChangeReason.STAFFING)

    assert (JobType.SEND_TEAM_CANCEL, ChannelKind.EMAIL) in _messages(uow, booking_id)


async def test_a_booking_awaiting_a_new_time_can_still_be_moved_or_cancelled(
    planner: PlannerService, bookings: BookingService, clock: FixedClock
) -> None:
    first = await _book(bookings)
    second = await _book(bookings)
    for booking_id in (first, second):
        await planner.request_rebook(GROUP_ADMIN, "demo", booking_id, ChangeReason.STAFFING)
    clock.advance(SLOT_0900 - NOW)  # its old time has come: it does not go ahead anyway

    moved = await planner.move(GROUP_ADMIN, "demo", first, SLOT_0930 + timedelta(days=7))
    await planner.cancel(GROUP_ADMIN, "demo", second, ChangeReason.UNFORESEEN)

    assert moved.status is BookingStatus.CONFIRMED
    with pytest.raises(DomainError, match="booking_not_active"):
        await planner.request_rebook(GROUP_ADMIN, "demo", second, ChangeReason.STAFFING)


async def test_a_bulk_request_takes_the_last_made_bookings_as_many_as_short(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    ids = []
    for _ in range(2):
        ids.append(await _book(bookings))
        clock.advance(timedelta(minutes=1))
    _staff(uow, 1)

    candidates = await planner.slot_candidates(GROUP_ADMIN, "demo", SLOT_0900)
    done = await planner.rebook_slot(
        GROUP_ADMIN, "demo", SLOT_0900, ChangeReason.STAFFING, frozenset({ids[1]})
    )

    assert [b.id for b in candidates] == [ids[1]]  # the newest one
    assert [b.id for b in done] == [ids[1]]
    assert uow.state.bookings[ids[0]].status is BookingStatus.CONFIRMED
    with pytest.raises(DomainError, match="slot_unavailable"):  # no shortfall left
        await planner.slot_candidates(GROUP_ADMIN, "demo", SLOT_0900)


async def test_a_bulk_request_refuses_when_the_slot_changed_since_the_confirmation(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    first = await _book(bookings)
    await _book(bookings)
    _staff(uow, 1)

    with pytest.raises(DomainError, match="slot_changed"):
        await planner.rebook_slot(
            GROUP_ADMIN, "demo", SLOT_0900, ChangeReason.STAFFING, frozenset({first})
        )
    assert all(b.status is BookingStatus.CONFIRMED for b in uow.state.bookings.values())


async def test_only_who_may_manage_asks_to_rebook(
    planner: PlannerService, bookings: BookingService
) -> None:
    booking_id = await _book(bookings)

    with pytest.raises(DomainError, match="not_permitted"):
        await planner.request_rebook(OTHER_TEAM, "demo", booking_id, ChangeReason.STAFFING)
    with pytest.raises(DomainError, match="not_permitted"):
        await planner.slot_candidates(AUDITOR, "demo", SLOT_0900)
    with pytest.raises(DomainError, match="not_permitted"):
        await planner.waiting(AUDITOR, "demo")
