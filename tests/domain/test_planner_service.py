from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.activity_alert import CHANGES_ALERT
from app.domain.admin import AdminService
from app.domain.agenda_service import AgendaService
from app.domain.audit import SYSTEM_ACTOR, Outcome, Source
from app.domain.authorization import Principal
from app.domain.booking import BookingStatus, ChangeReason
from app.domain.booking_changes import ensure_planner_may_request_rebook
from app.domain.channels import ChannelKind
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef
from app.domain.errors import DomainError
from app.domain.job_plan import JobType
from app.domain.planner_service import PlannerService, ReviewScope
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from app.domain.usage import ActorClass, FactKind, UsageFact
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, OTHER_TEAM
from tests.support.domain import (
    ADA,
    MONDAY,
    NOW,
    SLOT_0900,
    SLOT_0930,
    another_participant,
    book_another,
    book_confirmed,
    book_pending,
    window,
    with_team_invite,
)

SMS = frozenset({ChannelKind.SMS})


async def _book(
    bookings: BookingService, start=SLOT_0900, slug: str = "demo", consents=frozenset()
) -> int:
    return (await book_another(bookings, start, slug, consents)).booking.id or 0


def _staff(uow: InMemoryUnitOfWork, agents: int) -> None:
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=agents))]


def _messages(uow: InMemoryUnitOfWork, booking_id: int) -> set[tuple[JobType, ChannelKind | None]]:
    return {(j.type, j.channel) for j in uow.state.jobs[booking_id] if j.channel is not None}


# --- move ---------------------------------------------------------------------------------


async def test_a_move_places_a_new_booking_and_tells_the_participant(
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


async def test_a_cancellation_carries_the_reason_to_the_participant(
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
    uow.state.bookings[booking_id] = replace(
        uow.state.bookings[booking_id],
        contact_center_task=ContactCenterTaskRef("t-1", ContactCenterKind.WEBEX_CC),
    )

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
    admin = AdminService(uow, clock, alert_thresholds={CHANGES_ALERT: 1})
    planner = PlannerService(uow, clock, admin, bookings)
    ids = [await _book(bookings, start) for start in (SLOT_0900, SLOT_0900, SLOT_0930)]

    for booking_id in ids:
        await planner.cancel(GROUP_ADMIN, "demo", booking_id, ChangeReason.UNFORESEEN)

    alerts = [e for e in uow.state.audit if e.action == CHANGES_ALERT.action]
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


async def test_asking_to_rebook_frees_the_place_and_tells_the_participant(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings, consents=SMS)

    waiting = await planner.request_rebook(GROUP_ADMIN, "demo", booking_id, ChangeReason.STAFFING)

    assert waiting.status is BookingStatus.AWAITING_REBOOK
    assert waiting.rebook_by == SLOT_0900 + timedelta(days=14)
    assert (JobType.SEND_REBOOK_REQUEST, ChannelKind.SMS) in _messages(uow, booking_id)
    assert [w.booking.id for w in await planner.waiting(GROUP_ADMIN, "demo")] == [booking_id]
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
    assert done == 1
    assert uow.state.bookings[ids[1]].status is BookingStatus.AWAITING_REBOOK
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


async def test_the_rebook_form_finds_no_booking_of_another_group_whatever_its_status(
    planner: PlannerService, bookings: BookingService
) -> None:
    """The group check comes before the rule, which would answer ``booking_not_active``."""
    receipt = await book_pending(bookings, slug="andere-team", contact=another_participant())
    rule = ensure_planner_may_request_rebook

    with pytest.raises(DomainError, match="booking_not_found"):
        await planner.booking(GROUP_ADMIN, "demo", receipt.booking.id or 0, rule=rule)
    assert receipt.booking.status is BookingStatus.PENDING_CONFIRMATION


# --- confirm on the participant's behalf (ADR 0056 point 11) --------------------------------


async def test_a_planner_confirms_for_the_participant_who_gets_the_full_confirmation(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book_pending(bookings, slug="demo", contact=another_participant())
    booking_id = receipt.booking.id or 0

    confirmed = await planner.confirm(GROUP_ADMIN, "demo", booking_id)

    assert confirmed.status is BookingStatus.CONFIRMED
    assert (JobType.SEND_CONFIRMATION, ChannelKind.EMAIL) in _messages(uow, booking_id)
    [event] = [e for e in uow.state.audit if e.detail == "booking.confirmed_by_staff"]
    assert (event.actor, event.outcome, event.booking_id) == (
        GROUP_ADMIN.subject,
        Outcome.SUCCESS,
        booking_id,
    )


async def test_only_a_pending_booking_of_the_own_group_can_be_confirmed(
    planner: PlannerService, bookings: BookingService
) -> None:
    confirmed = await _book(bookings)
    other = await _book(bookings, slug="andere-team")
    with pytest.raises(DomainError) as not_pending:
        await planner.confirm(GROUP_ADMIN, "demo", confirmed)
    with pytest.raises(DomainError) as elsewhere:
        await planner.confirm(GROUP_ADMIN, "demo", other)
    with pytest.raises(DomainError) as refused:
        await planner.confirm(AUDITOR, "demo", confirmed)

    assert not_pending.value.code.value == "booking_not_pending"
    assert elsewhere.value.code.value == "booking_not_found"
    assert refused.value.code.value == "not_permitted"


async def test_after_its_deadline_a_pending_booking_lapses_instead(
    planner: PlannerService, bookings: BookingService, clock: FixedClock, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book_pending(bookings, slug="demo", contact=another_participant())
    booking_id = receipt.booking.id or 0
    confirm_by = receipt.booking.confirm_by
    assert confirm_by is not None
    clock.advance(confirm_by - clock.now() + timedelta(minutes=1))

    result = await planner.confirm(GROUP_ADMIN, "demo", booking_id)

    assert result.status is not BookingStatus.CONFIRMED
    assert not [e for e in uow.state.audit if e.detail == "booking.confirmed_by_staff"]


async def test_a_pending_booking_holds_its_place_in_the_agenda(
    bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """ADR 0056 point 11: counted towards occupancy and shortfall, and marked as pending."""
    await _book(bookings)
    await book_pending(bookings, slug="demo", contact=another_participant())

    agenda = AgendaService(uow, clock, AdminService(uow, clock), horizon_days=14)
    days = await agenda.days(GROUP_ADMIN, "demo", SLOT_0900.date(), 1)

    [slot] = [s for s in days[0].slots if s.start == SLOT_0900]
    assert (slot.booked, slot.pending) == (2, 1)


async def test_a_bulk_request_never_asks_an_older_booking_for_a_newer_pending_one(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """ADR 0044 §5: the last made go first, a pending one too; it is left to lapse or confirm."""
    older = await _book(bookings)
    clock.advance(timedelta(minutes=1))
    await book_pending(bookings, slug="demo", contact=another_participant())
    _staff(uow, 1)

    candidates = await planner.slot_candidates(GROUP_ADMIN, "demo", SLOT_0900)

    assert older not in [b.id for b in candidates]


async def test_a_pending_last_made_booking_leaves_nobody_to_ask_in_bulk_or_in_review(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """ADR 0056 point 11: the slot's excess place is the pending one's, which is never asked."""
    await _book(bookings)
    clock.advance(timedelta(minutes=1))
    await book_pending(bookings, slug="demo", contact=another_participant())
    _staff(uow, 1)

    candidates = await planner.slot_candidates(GROUP_ADMIN, "demo", SLOT_0900)
    reviewed = await planner.review_proposals(GROUP_ADMIN, "demo", ReviewScope.slot(SLOT_0900))

    assert (candidates, reviewed) == ([], [])


async def test_a_confirmation_by_a_planner_is_counted_as_the_planners(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    """The reports tell a planner's confirmation from the participant's own (metric U9)."""
    receipt = await book_pending(bookings, slug="demo", contact=another_participant())

    await planner.confirm(GROUP_ADMIN, "demo", receipt.booking.id or 0)

    [fact] = [
        f
        for f in uow.state.usage_facts
        if isinstance(f, UsageFact) and f.kind is FactKind.BOOKING_CONFIRMED
    ]
    assert fact.actor_class is ActorClass.PLANNER


# --- move and cancel a pending booking (ADR 0056 step 3b) -----------------------------------


async def test_a_moved_pending_booking_stays_pending_with_its_own_deadline(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book_pending(bookings, slug="demo", contact=another_participant())

    moved = await planner.move(GROUP_ADMIN, "demo", receipt.booking.id or 0, SLOT_0930)

    assert moved.status is BookingStatus.PENDING_CONFIRMATION
    assert moved.start == SLOT_0930
    assert moved.confirm_by is not None
    old = uow.state.bookings[receipt.booking.id or 0]
    assert old.status is not BookingStatus.PENDING_CONFIRMATION


async def test_a_pending_booking_can_be_cancelled_but_not_asked_to_rebook(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    first = await book_pending(bookings, slug="demo", contact=another_participant())
    second = await book_pending(bookings, slug="demo", contact=another_participant())

    await planner.cancel(GROUP_ADMIN, "demo", first.booking.id or 0, ChangeReason.STAFFING)
    with pytest.raises(DomainError, match="booking_not_active"):
        await planner.request_rebook(
            GROUP_ADMIN, "demo", second.booking.id or 0, ChangeReason.STAFFING
        )

    assert uow.state.bookings[first.booking.id or 0].status is BookingStatus.CANCELLED
    assert uow.state.bookings[second.booking.id or 0].status is BookingStatus.PENDING_CONFIRMATION


async def test_a_pending_booking_is_told_by_email_only_and_the_team_gets_no_withdrawal(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    """ADR 0056: never sent to the team nor by SMS, so nothing of either on a change."""
    uow.state.groups[1] = with_team_invite(uow.state.groups[1])
    contact = replace(another_participant(), phone="+31612345678")
    moving = await book_pending(bookings, slug="demo", contact=another_participant())
    cancelling = await book_pending(bookings, slug="demo", contact=contact)

    moved = await planner.move(GROUP_ADMIN, "demo", moving.booking.id or 0, SLOT_0930)
    await planner.cancel(GROUP_ADMIN, "demo", cancelling.booking.id or 0, ChangeReason.STAFFING)

    old_jobs = {j.type for j in uow.state.jobs[moving.booking.id or 0]}
    new_jobs = {j.type for j in uow.state.jobs[moved.id or 0]}
    cancel_jobs = _messages(uow, cancelling.booking.id or 0)
    assert JobType.SEND_TEAM_CANCEL not in old_jobs
    assert JobType.SEND_CONFIRM_REQUEST in new_jobs
    assert JobType.SEND_CHANGE not in new_jobs
    assert cancel_jobs == {(JobType.SEND_CANCELLATION, ChannelKind.EMAIL)}
    assert JobType.SEND_TEAM_CANCEL not in {
        j.type for j in uow.state.jobs[cancelling.booking.id or 0]
    }
