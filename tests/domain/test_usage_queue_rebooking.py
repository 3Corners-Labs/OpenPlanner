"""Queue claims and the rebooking funnel write their facts in the change's transaction."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain import booking_changes
from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.booking import BookingStatus, ChangeReason, MeetingRef
from app.domain.errors import DomainError
from app.domain.planner_service import PlannerService
from app.domain.queue_service import QueueService
from app.domain.scheduling import DispatchKind
from app.domain.services import BookingService
from app.domain.usage import ActorClass, FactKind, UsageFact
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryTransaction, InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN
from tests.support.domain import SLOT_0900, SLOT_0930, book, participant
from tests.support.principals import principal

AGENT = principal("a-1", "A", frozenset({Role.AGENT}), frozenset(), frozenset({"demo"}))
KEY = b"p" * 32


def _facts(uow: InMemoryUnitOfWork, *kinds: FactKind) -> list[UsageFact]:
    return [f for f in uow.state.usage_facts if isinstance(f, UsageFact) and f.kind in kinds]


# --- the own queue -------------------------------------------------------------------------


@pytest.fixture
def queue(uow: InMemoryUnitOfWork, clock: FixedClock) -> QueueService:
    group = uow.state.groups[1]
    uow.state.groups[1] = replace(group, dispatch_kind=DispatchKind.OPENPLANNER_QUEUE)
    return QueueService(uow, clock, AdminService(uow, clock), KEY)


async def _queued_booking(uow: InMemoryUnitOfWork, clock: FixedClock) -> int:
    receipt = await book(BookingService(uow, clock, b"k" * 32), SLOT_0900, "demo")
    booking_id = receipt.booking.id or 0
    uow.state.bookings[booking_id] = replace(
        uow.state.bookings[booking_id], meeting=MeetingRef("m", "https://webex/j")
    )
    return booking_id


async def test_a_claim_and_a_release_are_facts_with_the_time_since_it_was_visible(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _queued_booking(uow, clock)
    visible = uow.state.groups[1].queue_visible_from_minutes
    clock.advance(SLOT_0900 - timedelta(minutes=visible) + timedelta(minutes=3) - clock.now())

    await queue.claim_next(AGENT, "demo")
    await queue.release(AGENT, "demo", booking_id)

    claimed, released = _facts(uow, FactKind.QUEUE_CLAIMED, FactKind.QUEUE_RELEASED)
    assert claimed.value_seconds == 180
    assert (claimed.dispatch_kind, released.kind) == (
        DispatchKind.OPENPLANNER_QUEUE,
        FactKind.QUEUE_RELEASED,
    )
    assert claimed.actor_class is None  # no person, not even a class of one


async def test_a_claim_that_loses_the_race_writes_no_fact(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await _queued_booking(uow, clock)
    clock.advance(SLOT_0900 - clock.now())
    await queue.claim_next(AGENT, "demo")

    with pytest.raises(DomainError):
        await queue.claim_next(AGENT, "demo")

    assert len(_facts(uow, FactKind.QUEUE_CLAIMED)) == 1


async def test_the_claim_fact_is_rolled_back_with_the_claim(
    queue: QueueService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _queued_booking(uow, clock)
    clock.advance(SLOT_0900 - clock.now())

    async def broken(self: InMemoryTransaction, event: object) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(InMemoryTransaction, "add_audit_event", broken)
    with pytest.raises(RuntimeError):
        await queue.claim_next(AGENT, "demo")

    assert _facts(uow, FactKind.QUEUE_CLAIMED) == []


# --- the rebooking funnel ------------------------------------------------------------------


async def test_a_request_accepted_and_one_that_lapses_are_the_funnel(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    accepted = await book(bookings, SLOT_0900, "demo")
    lapsing = await book(bookings, SLOT_0900, "demo", contact=participant(2))
    for receipt in (accepted, lapsing):
        await planner.request_rebook(
            GROUP_ADMIN, "demo", receipt.booking.id or 0, ChangeReason.STAFFING
        )
    clock.advance(timedelta(hours=5, minutes=20))

    await bookings.reschedule(accepted.management_token, SLOT_0930 + timedelta(days=7))
    async with uow.locked() as tx:
        booking = await tx.get_booking(lapsing.booking.id or 0)
        assert booking is not None
        await booking_changes.lapse(tx, booking, clock.now())

    proposed, taken, expired = _facts(
        uow, FactKind.REBOOK_PROPOSED, FactKind.REBOOK_ACCEPTED, FactKind.REBOOK_EXPIRED
    )[0:1] + _facts(uow, FactKind.REBOOK_ACCEPTED, FactKind.REBOOK_EXPIRED)
    assert (proposed.actor_class, proposed.reason_code) == (ActorClass.PLANNER, "staffing")
    assert taken.value_seconds == 5 * 3600 + 15 * 60  # the quarter hours it took
    assert taken.actor_class is ActorClass.PARTICIPANT
    assert (expired.actor_class, expired.reason_code) == (ActorClass.SYSTEM, "staffing")
    assert len(_facts(uow, FactKind.REBOOK_PROPOSED)) == 2


async def test_a_planner_moving_a_waiting_booking_is_no_acceptance(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(bookings, SLOT_0900, "demo")
    booking_id = receipt.booking.id or 0
    await planner.request_rebook(GROUP_ADMIN, "demo", booking_id, ChangeReason.STAFFING)

    await planner.move(GROUP_ADMIN, "demo", booking_id, SLOT_0930 + timedelta(days=7))

    assert _facts(uow, FactKind.REBOOK_ACCEPTED) == []
    assert uow.state.bookings[booking_id].status is BookingStatus.CANCELLED


async def test_a_bulk_request_that_ends_the_shortfall_writes_resolved_once(
    planner: PlannerService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    from app.domain.scheduling import ScheduleRule
    from tests.support.domain import window

    ids = []
    for number in (1, 2):
        receipt = await book(bookings, SLOT_0900, "demo", contact=participant(number))
        ids.append(receipt.booking.id or 0)
    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    (candidate,) = await planner.slot_candidates(GROUP_ADMIN, "demo", SLOT_0900)

    await planner.rebook_slot(
        GROUP_ADMIN, "demo", SLOT_0900, ChangeReason.STAFFING, frozenset({candidate.id or 0})
    )

    (fact,) = _facts(uow, FactKind.SLOT_SHORTFALL)
    assert (fact.reason_code, fact.capacity, fact.booked, fact.bucket_hour) == (
        "resolved",
        1,
        1,
        SLOT_0900,
    )
