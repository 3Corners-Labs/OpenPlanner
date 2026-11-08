"""A held proposal of a new time (ADR 0057 step 2): hold, answer, release and expiry.

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; group ``demo`` is staffed on Mondays
09:00-10:00 Amsterdam by 2 agents, so Monday 5 January and Monday 12 January have 09:00 and
09:30 slots of two places each.
"""

from datetime import timedelta

import pytest

from app.domain import booking_changes
from app.domain.booking import BookingStatus, ChangeReason
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import JobType
from app.domain.planner_service import PlannerService
from app.domain.reschedule_proposal import (
    HeldProposal,
    ProposalOutcome,
    proposal_deadline,
    proposal_reminder,
)
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, book_another

NEXT_0900 = SLOT_0900 + timedelta(days=7)
NEXT_0930 = SLOT_0930 + timedelta(days=7)
CONFIRM_BY = NOW + timedelta(hours=48)


async def _book(bookings: BookingService, start=SLOT_0900) -> tuple[int, str]:
    receipt = await book_another(bookings, start)
    return receipt.booking.id or 0, receipt.management_token


def _jobs(uow: InMemoryUnitOfWork, booking_id: int) -> list[tuple[JobType, object]]:
    return [(j.type, j.pinned_start) for j in uow.state.jobs[booking_id]]


# --- the pure rules -------------------------------------------------------------------------


def test_the_deadline_never_passes_the_change_deadline_or_the_lapse() -> None:
    later = NOW + timedelta(days=5)
    assert proposal_deadline(NOW, 48, later, later) == NOW + timedelta(hours=48)
    assert proposal_deadline(NOW, 48, NOW + timedelta(hours=6), later) == NOW + timedelta(hours=6)
    assert proposal_deadline(NOW, 48, later, NOW + timedelta(hours=30)) == NOW + timedelta(hours=30)


def test_the_reminder_goes_before_the_deadline_unless_off_or_past() -> None:
    assert proposal_reminder(NOW, CONFIRM_BY, 12) == CONFIRM_BY - timedelta(hours=12)
    assert proposal_reminder(NOW, CONFIRM_BY, 0) is None
    assert proposal_reminder(NOW, NOW + timedelta(hours=6), 12) is None


def test_a_proposal_is_due_only_while_open_and_past_its_deadline() -> None:
    held = HeldProposal(1, 1, 1, NEXT_0900, NEXT_0930, CONFIRM_BY, NOW)

    assert not held.is_due(CONFIRM_BY - timedelta(seconds=1))
    assert held.is_due(CONFIRM_BY)
    assert not held.released(NOW, ProposalOutcome.WITHDRAWN).is_due(CONFIRM_BY)


# --- holding --------------------------------------------------------------------------------


async def test_a_proposal_frees_the_old_place_and_holds_the_new_one(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, _token = await _book(bookings)

    proposal = await planner.propose(
        GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING
    )

    booking = uow.state.bookings[booking_id]
    assert booking.status is BookingStatus.AWAITING_REBOOK
    assert booking.change_reason is ChangeReason.STAFFING
    assert (proposal.start, proposal.confirm_by, proposal.is_open) == (NEXT_0900, CONFIRM_BY, True)
    async with uow.read() as tx:
        assert await tx.count_active_bookings(proposal.slot_id) == 1
        assert await tx.count_active_bookings(booking.slot_id) == 0
    (event,) = [e for e in uow.state.audit if e.detail.startswith("planner.propose")]
    assert (event.detail, event.booking_id) == ("planner.propose,reason:staffing", booking_id)


async def test_the_jobs_are_the_proposal_its_reminder_and_expiry_instead_of_the_request(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, _token = await _book(bookings)

    await planner.propose(GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING)

    jobs = _jobs(uow, booking_id)
    assert (JobType.SEND_RESCHEDULE_PROPOSAL, NEXT_0900) in jobs
    assert (JobType.SEND_PROPOSAL_REMINDER, NEXT_0900) in jobs
    assert (JobType.EXPIRE_PROPOSAL, NEXT_0900) in jobs
    assert {JobType.LAPSE_REBOOK, JobType.SEND_REBOOK_REMINDER} <= {t for t, _ in jobs}
    assert JobType.SEND_REBOOK_REQUEST not in {t for t, _ in jobs}
    expiry = next(j for j in uow.state.jobs[booking_id] if j.type is JobType.EXPIRE_PROPOSAL)
    assert expiry.run_at == CONFIRM_BY


async def test_the_held_place_counts_for_everyone_else(
    planner: PlannerService, bookings: BookingService
) -> None:
    booking_id, _token = await _book(bookings)
    await _book(bookings, NEXT_0900)
    await planner.propose(GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING)

    with pytest.raises(DomainError) as caught:
        await _book(bookings, NEXT_0900)

    assert caught.value.code is ErrorCode.SLOT_FULL
    free = await bookings.availability("demo", NEXT_0900.date(), NEXT_0900.date())
    assert NEXT_0900 not in {slot.start for slot in free}


@pytest.mark.parametrize(
    ("start", "code"),
    [
        (SLOT_0900, ErrorCode.SAME_SLOT),
        (NOW + timedelta(minutes=30), ErrorCode.BOOKING_LOCKED),
    ],
)
async def test_a_proposal_needs_another_slot_a_planner_may_still_fill(
    planner: PlannerService, bookings: BookingService, start, code: ErrorCode
) -> None:
    booking_id, _token = await _book(bookings)

    with pytest.raises(DomainError) as caught:
        await planner.propose(GROUP_ADMIN, "demo", booking_id, start, ChangeReason.STAFFING)

    assert caught.value.code is code


async def test_a_full_slot_cannot_be_proposed(
    planner: PlannerService, bookings: BookingService
) -> None:
    booking_id, _token = await _book(bookings)
    await _book(bookings, NEXT_0900)
    await _book(bookings, NEXT_0900)

    with pytest.raises(DomainError) as caught:
        await planner.propose(GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING)

    assert caught.value.code is ErrorCode.SLOT_FULL


async def test_only_a_planner_may_propose(
    planner: PlannerService, bookings: BookingService
) -> None:
    booking_id, _token = await _book(bookings)

    with pytest.raises(DomainError) as caught:
        await planner.propose(AUDITOR, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING)

    assert caught.value.code is ErrorCode.NOT_PERMITTED


# --- answering ------------------------------------------------------------------------------


async def test_confirming_moves_the_booking_into_the_proposed_slot(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, token = await _book(bookings)
    proposal = await planner.propose(
        GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING
    )

    receipt = await bookings.confirm_proposal(token)

    new = receipt.booking
    assert (new.start, new.status, new.rescheduled_from_id) == (
        NEXT_0900,
        BookingStatus.CONFIRMED,
        booking_id,
    )
    assert uow.state.bookings[booking_id].status is BookingStatus.CANCELLED
    assert uow.state.proposals[proposal.id or 0].outcome is ProposalOutcome.CONFIRMED
    async with uow.read() as tx:
        assert await tx.count_active_bookings(proposal.slot_id) == 1  # the booking, not the hold
    assert JobType.SEND_CHANGE in {j.type for j in uow.state.jobs[new.id or 0]}


async def test_choosing_another_time_releases_the_hold(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, token = await _book(bookings)
    proposal = await planner.propose(
        GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING
    )

    receipt = await bookings.reschedule(token, NEXT_0930)

    assert receipt.booking.start == NEXT_0930
    assert uow.state.proposals[proposal.id or 0].outcome is ProposalOutcome.CHOSEN
    async with uow.read() as tx:
        assert await tx.count_active_bookings(proposal.slot_id) == 0


async def test_cancelling_releases_the_hold(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, token = await _book(bookings)
    proposal = await planner.propose(
        GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING
    )

    await bookings.cancel(token)

    assert uow.state.proposals[proposal.id or 0].outcome is ProposalOutcome.CANCELLED
    assert uow.state.bookings[booking_id].status is BookingStatus.CANCELLED


async def test_the_details_show_the_open_proposal_until_its_deadline(
    planner: PlannerService, bookings: BookingService, clock: FixedClock
) -> None:
    booking_id, token = await _book(bookings)
    await planner.propose(GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING)

    shown = (await bookings.find(token)).proposal
    clock.advance(timedelta(hours=48))
    gone = (await bookings.find(token)).proposal

    assert shown is not None
    assert shown.start == NEXT_0900
    assert gone is None


# --- expiry ---------------------------------------------------------------------------------


async def test_after_the_deadline_the_hold_is_released_and_choosing_stays_possible(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id, token = await _book(bookings)
    proposal = await planner.propose(
        GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING
    )
    clock.advance(timedelta(hours=48))

    with pytest.raises(DomainError) as caught:
        await bookings.confirm_proposal(token)
    async with uow.locked() as tx:
        expired = await booking_changes.expire_proposal(
            tx, uow.state.bookings[booking_id], clock.now()
        )
        again = await booking_changes.expire_proposal(
            tx, uow.state.bookings[booking_id], clock.now()
        )

    assert caught.value.code is ErrorCode.PROPOSAL_NOT_OPEN
    assert (expired, again) == (True, False)
    assert uow.state.proposals[proposal.id or 0].outcome is ProposalOutcome.EXPIRED
    assert uow.state.bookings[booking_id].status is BookingStatus.AWAITING_REBOOK
    assert JobType.SEND_PROPOSAL_RELEASED in {j.type for j in uow.state.jobs[booking_id]}
    receipt = await bookings.reschedule(token, NEXT_0930)
    assert receipt.booking.start == NEXT_0930


async def test_an_expiry_before_the_deadline_does_nothing(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id, _token = await _book(bookings)
    await planner.propose(GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING)

    async with uow.locked() as tx:
        expired = await booking_changes.expire_proposal(
            tx, uow.state.bookings[booking_id], clock.now()
        )

    assert not expired


async def test_the_lapse_releases_an_open_hold(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id, _token = await _book(bookings)
    proposal = await planner.propose(
        GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING
    )

    async with uow.locked() as tx:
        await booking_changes.lapse(tx, uow.state.bookings[booking_id], clock.now())

    assert uow.state.proposals[proposal.id or 0].outcome is ProposalOutcome.EXPIRED


# --- suggestions ----------------------------------------------------------------------------


async def test_suggestions_leave_out_the_held_time_and_follow_the_ranking(
    planner: PlannerService, bookings: BookingService
) -> None:
    booking_id, token = await _book(bookings)
    await planner.propose(GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING)

    suggested = await bookings.suggestions(token)

    # Same time of day first (two weeks on, the held one a week on is left out), then 30
    # minutes off, nearest day first.
    assert suggested == [SLOT_0900 + timedelta(days=14), SLOT_0930, NEXT_0930]


async def test_a_booking_that_goes_ahead_gets_no_suggestions(bookings: BookingService) -> None:
    _booking_id, token = await _book(bookings)

    assert await bookings.suggestions(token) == []


# --- the planner's review (step 3) ----------------------------------------------------------


def _short(uow: InMemoryUnitOfWork) -> None:
    """Staff Mondays with one agent, so a slot with two bookings is one short."""
    from app.domain.scheduling import ScheduleRule
    from tests.support.domain import window

    for group_id in uow.state.groups:
        uow.state.rules[group_id] = [ScheduleRule(weekday=0, window=window(capacity=1))]


async def test_the_agenda_counts_a_held_proposal_like_a_pending_booking(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    from app.domain.agenda_service import agenda_days

    booking_id, _token = await _book(bookings)
    await planner.propose(GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING)

    async with uow.read() as tx:
        group = uow.state.groups[1]
        (day,) = await agenda_days(tx, group, NEXT_0900.date(), 1, clock.now())
    slot = next(s for s in day.slots if s.start == NEXT_0900)
    assert (slot.booked, slot.pending) == (1, 1)


async def test_the_review_proposes_the_last_made_booking_with_the_free_times(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await _book(bookings)
    last, _token = await _book(bookings)
    _short(uow)

    (row,) = await planner.slot_proposals(GROUP_ADMIN, "demo", SLOT_0900)

    assert row.booking.id == last
    assert row.proposed == NEXT_0900  # same time of day, a week on
    assert row.options[0] == NEXT_0900
    assert SLOT_0930 in row.options


async def test_sending_the_review_holds_asks_and_audits_each_booking(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    first, _ = await _book(bookings)
    second, _ = await _book(bookings)
    from app.domain.scheduling import ScheduleRule
    from tests.support.domain import window

    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=0, closes="09:30"))] + [
        ScheduleRule(weekday=0, window=window(opens="09:30", closes="10:00", capacity=2))
    ]

    sent = await planner.propose_slot(
        GROUP_ADMIN, "demo", SLOT_0900, ChangeReason.STAFFING, {first: NEXT_0930, second: None}
    )

    assert sent == 2
    assert uow.state.bookings[first].status is BookingStatus.AWAITING_REBOOK
    assert uow.state.bookings[second].status is BookingStatus.AWAITING_REBOOK
    async with uow.read() as tx:
        held = await tx.get_open_proposal(first)
        assert held is not None
        assert held.start == NEXT_0930
        assert await tx.get_open_proposal(second) is None
    details = [e.detail for e in uow.state.audit if e.detail.startswith("planner.")]
    assert sorted(details) == [
        "planner.propose,reason:staffing,bulk",
        "planner.rebook,reason:staffing,bulk",
    ]


async def test_a_review_of_a_changed_slot_sends_nothing(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await _book(bookings)
    last, _ = await _book(bookings)
    _short(uow)
    await _book(bookings, NEXT_0900)  # the proposed slot fills up meanwhile

    with pytest.raises(DomainError) as caught:
        await planner.propose_slot(
            GROUP_ADMIN, "demo", SLOT_0900, ChangeReason.STAFFING, {last: NEXT_0900}
        )
    with pytest.raises(DomainError) as other:
        await planner.propose_slot(
            GROUP_ADMIN, "demo", SLOT_0900, ChangeReason.STAFFING, {999: NEXT_0930}
        )

    assert caught.value.code is ErrorCode.SLOT_CHANGED
    assert other.value.code is ErrorCode.SLOT_CHANGED
    assert uow.state.bookings[last].status is BookingStatus.CONFIRMED
    assert uow.state.proposals == {}


async def test_the_planner_withdraws_or_confirms_a_proposal(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    withdrawn, _ = await _book(bookings)
    confirmed, _ = await _book(bookings)
    await planner.propose(GROUP_ADMIN, "demo", withdrawn, NEXT_0900, ChangeReason.STAFFING)
    await planner.propose(GROUP_ADMIN, "demo", confirmed, NEXT_0930, ChangeReason.STAFFING)

    waiting = await planner.waiting(GROUP_ADMIN, "demo")
    await planner.withdraw(GROUP_ADMIN, "demo", withdrawn)
    moved = await planner.confirm_proposal(GROUP_ADMIN, "demo", confirmed)

    assert {w.booking.id: w.proposal.start for w in waiting if w.proposal} == {
        withdrawn: NEXT_0900,
        confirmed: NEXT_0930,
    }
    assert uow.state.bookings[withdrawn].status is BookingStatus.AWAITING_REBOOK
    async with uow.read() as tx:
        assert await tx.get_open_proposal(withdrawn) is None
    assert (moved.start, moved.status) == (NEXT_0930, BookingStatus.CONFIRMED)
    with pytest.raises(DomainError) as caught:
        await planner.withdraw(GROUP_ADMIN, "demo", withdrawn)
    assert caught.value.code is ErrorCode.PROPOSAL_NOT_OPEN
    details = {e.detail for e in uow.state.audit}
    assert {"planner.withdraw", "planner.confirm_proposal"} <= details


async def test_another_groups_booking_cannot_be_withdrawn_through_this_group(
    planner: PlannerService, bookings: BookingService
) -> None:
    booking_id, _ = await _book(bookings)
    await planner.propose(GROUP_ADMIN, "demo", booking_id, NEXT_0900, ChangeReason.STAFFING)

    with pytest.raises(DomainError) as caught:
        await planner.withdraw(GROUP_ADMIN, "demo-twee", booking_id)

    assert caught.value.code in {ErrorCode.BOOKING_NOT_FOUND, ErrorCode.NOT_PERMITTED}
