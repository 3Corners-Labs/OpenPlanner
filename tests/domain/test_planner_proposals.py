"""A planner proposes a new time and the citizen answers it (ADR 0057 step 2).

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; the demo group is staffed on Mondays
09:00-11:00 Amsterdam time. A proposal is held 48 hours by default, with one reminder 12 hours
before its deadline.
"""

import itertools
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.booking import BookingStatus, ChangeReason
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError
from app.domain.job_plan import PURGE_AFTER, JobType, PlannedJob
from app.domain.planner_service import PlannerService
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingReceipt, BookingService
from app.domain.tokens import derive_token
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN
from tests.support.domain import (
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    book_confirmed,
    citizen,
    window,
)

REASON = ChangeReason.STAFFING
CONFIRM_BY = NOW + timedelta(hours=48)
SLOT_1000 = SLOT_0900 + timedelta(hours=1)
_CITIZENS = itertools.count(100)


@pytest.fixture
def planner(uow: InMemoryUnitOfWork, clock: FixedClock, bookings: BookingService) -> PlannerService:
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window("09:00", "11:00", 2))]
    return PlannerService(uow, clock, AdminService(uow, clock), bookings, alert_per_hour=50)


async def _book(bookings: BookingService, start=SLOT_0900, consents=frozenset()) -> BookingReceipt:
    return await book_confirmed(
        bookings,
        group_slug="demo",
        start=start,
        contact=citizen(next(_CITIZENS)),
        locale="nl",
        consents=consents,
    )


def _types(uow: InMemoryUnitOfWork, booking_id: int | None) -> list[JobType]:
    return [job.type for job in uow.state.jobs[booking_id or 0]]


def _token(uow: InMemoryUnitOfWork, booking_id: int | None) -> str:
    return derive_token(TEST_TOKEN_KEY, uow.state.bookings[booking_id or 0].token_salt)


async def test_a_proposal_holds_the_new_time_and_frees_the_old_one(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await _book(bookings, consents=frozenset({ChannelKind.SMS}))
    old_id = old.booking.id

    proposal = await planner.propose(GROUP_ADMIN, "demo", old_id or 0, SLOT_0930, REASON)

    assert proposal.status is BookingStatus.PROPOSED
    assert (proposal.start, proposal.rescheduled_from_id) == (SLOT_0930, old_id)
    assert (proposal.change_reason, proposal.confirm_by) == (REASON, CONFIRM_BY)
    assert proposal.holds_place
    replaced = uow.state.bookings[old_id or 0]
    assert replaced.status is BookingStatus.AWAITING_REBOOK
    assert not replaced.holds_place
    # The proposal is the message: no separate request to rebook.
    assert JobType.SEND_REBOOK_REQUEST not in _types(uow, old_id)
    assert JobType.LAPSE_REBOOK in _types(uow, old_id)
    reminder = CONFIRM_BY - timedelta(hours=12)
    assert uow.state.jobs[proposal.id or 0] == [
        PlannedJob(JobType.SEND_PROPOSAL, NOW, None, ChannelKind.EMAIL),
        PlannedJob(JobType.SEND_PROPOSAL, NOW, None, ChannelKind.SMS),
        PlannedJob(JobType.SEND_PROPOSAL_REMINDER, reminder, None, ChannelKind.EMAIL),
        PlannedJob(JobType.SEND_PROPOSAL_REMINDER, reminder, None, ChannelKind.SMS),
        PlannedJob(JobType.EXPIRE_UNCONFIRMED, CONFIRM_BY, None),
        PlannedJob(JobType.PURGE_PII, CONFIRM_BY + PURGE_AFTER, None),
    ]
    audit = uow.state.audit[-1]
    assert (audit.detail, audit.booking_id) == ("planner.propose,reason:staffing", old_id)


async def test_the_proposal_is_held_no_longer_than_the_new_times_change_deadline(
    uow: InMemoryUnitOfWork, clock: FixedClock, bookings: BookingService
) -> None:
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    planner = PlannerService(
        uow, clock, AdminService(uow, clock), bookings, alert_per_hour=50, proposal_hours=168
    )
    old = await _book(bookings)

    proposal = await planner.propose(GROUP_ADMIN, "demo", old.booking.id or 0, SLOT_0930, REASON)

    group = uow.state.groups[1]
    assert proposal.confirm_by == group.change_deadline(SLOT_0930)


async def test_a_held_place_counts_against_the_slot(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await _book(bookings)
    await _book(bookings, SLOT_0930)
    await planner.propose(GROUP_ADMIN, "demo", old.booking.id or 0, SLOT_0930, REASON)

    with pytest.raises(DomainError, match="slot_full"):
        await _book(bookings, SLOT_0930)


async def test_confirming_the_proposal_moves_the_appointment(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await _book(bookings)
    proposal = await planner.propose(GROUP_ADMIN, "demo", old.booking.id or 0, SLOT_0930, REASON)

    confirmed = await bookings.confirm(_token(uow, proposal.id))

    assert confirmed.status is BookingStatus.CONFIRMED
    assert JobType.SEND_CHANGE in _types(uow, proposal.id)
    replaced = uow.state.bookings[old.booking.id or 0]
    assert replaced.status is BookingStatus.CANCELLED
    assert JobType.SEND_CANCELLATION not in _types(uow, old.booking.id)  # no second mail


async def test_choosing_another_time_closes_the_proposal_and_the_old_booking(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await _book(bookings)
    proposal = await planner.propose(GROUP_ADMIN, "demo", old.booking.id or 0, SLOT_0930, REASON)
    token = _token(uow, proposal.id)
    assert (await bookings.find(token)).changeable

    chosen = await bookings.reschedule(token, SLOT_0900)

    assert chosen.booking.status is BookingStatus.CONFIRMED
    assert chosen.booking.start == SLOT_0900
    assert uow.state.bookings[proposal.id or 0].status is BookingStatus.CANCELLED
    assert uow.state.bookings[old.booking.id or 0].status is BookingStatus.CANCELLED


async def test_cancelling_the_proposal_cancels_the_appointment(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await _book(bookings)
    proposal = await planner.propose(GROUP_ADMIN, "demo", old.booking.id or 0, SLOT_0930, REASON)

    await bookings.cancel(_token(uow, proposal.id))

    assert uow.state.bookings[proposal.id or 0].status is BookingStatus.CANCELLED
    assert JobType.SEND_CANCELLATION in _types(uow, proposal.id)
    assert uow.state.bookings[old.booking.id or 0].status is BookingStatus.CANCELLED


async def test_an_unanswered_proposal_is_released_and_the_citizen_can_still_choose(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    old = await _book(bookings)
    proposal = await planner.propose(GROUP_ADMIN, "demo", old.booking.id or 0, SLOT_0930, REASON)
    clock.advance(CONFIRM_BY - NOW)

    released = await bookings.confirm(_token(uow, proposal.id))

    assert released.released_proposal
    assert not released.lapsed_unconfirmed
    assert _types(uow, proposal.id)[-1] is JobType.SEND_PROPOSAL_RELEASED
    assert uow.state.bookings[old.booking.id or 0].status is BookingStatus.AWAITING_REBOOK
    details = await bookings.find(_token(uow, proposal.id))
    assert details.replaced_token == _token(uow, old.booking.id)
    await _book(bookings, SLOT_0930)  # the held place is free again
    chosen = await bookings.reschedule(details.replaced_token or "", SLOT_1000)
    assert chosen.booking.status is BookingStatus.CONFIRMED


async def test_moving_the_old_booking_withdraws_the_open_proposal(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await _book(bookings)
    old_id = old.booking.id or 0
    proposal = await planner.propose(GROUP_ADMIN, "demo", old_id, SLOT_0930, REASON)

    moved = await planner.move(GROUP_ADMIN, "demo", old_id, SLOT_1000)

    assert moved.status is BookingStatus.CONFIRMED
    withdrawn = uow.state.bookings[proposal.id or 0]
    assert withdrawn.status is BookingStatus.CANCELLED
    assert not withdrawn.released_proposal
    assert JobType.SEND_CANCELLATION not in _types(uow, proposal.id)


async def test_cancelling_the_old_booking_withdraws_the_open_proposal(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    old = await _book(bookings)
    old_id = old.booking.id or 0
    proposal = await planner.propose(GROUP_ADMIN, "demo", old_id, SLOT_0930, REASON)

    await planner.cancel(GROUP_ADMIN, "demo", old_id, REASON)

    assert uow.state.bookings[proposal.id or 0].status is BookingStatus.CANCELLED


async def test_a_proposal_needs_another_time_and_time_to_confirm(
    planner: PlannerService, bookings: BookingService, clock: FixedClock
) -> None:
    old = await _book(bookings)
    old_id = old.booking.id or 0

    with pytest.raises(DomainError, match="same_slot"):
        await planner.propose(GROUP_ADMIN, "demo", old_id, SLOT_0900, REASON)
    clock.advance(SLOT_0930 - NOW - timedelta(minutes=30))  # past both deadlines
    with pytest.raises(DomainError, match="planner_too_late"):
        await planner.propose(GROUP_ADMIN, "demo", old_id, SLOT_0930, REASON)
