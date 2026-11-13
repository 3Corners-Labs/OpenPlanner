"""Request work in the own queue: claim, lease, start, complete, release (ADR 0121 step 5b).

A flow without video on a counted pool of one unit (Mondays 09:00–12:00 Amsterdam), an
appointment of 30 minutes plus 5 minutes wrap-up at 09:00, and an agent of the service. The
same queue serves a legacy booking beside it, which must behave as before (A03, A19).
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.errors import DomainError, ErrorCode
from app.domain.queue_service import COMPLETED, STARTED, STARTED_IN_CONFLICT, QueueService
from app.domain.service_requests import HandlingState, WorkItemState
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW, SLOT_0900
from tests.support.request_work import (
    ADMIN,
    AGENT,
    FLOW,
    LENGTH,
    OTHER,
    POOL,
    VIDEO_FLOW,
    at,
    book,
    queue_of,
    unit_block,
    world,
)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return world()


@pytest.fixture
def queue(uow: InMemoryUnitOfWork, clock: FixedClock) -> QueueService:
    return queue_of(uow, clock)


async def refused(attempt: object) -> ErrorCode:
    with pytest.raises(DomainError) as excinfo:
        await attempt  # type: ignore[misc]
    return excinfo.value.code


def details(uow: InMemoryUnitOfWork) -> list[str]:
    """Return the audit details written so far."""
    return [e.detail for e in uow.state.audit if e.detail]


async def test_a_claim_makes_the_request_and_a_work_item_with_a_lease(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock)
    at(clock)

    claimed = await queue.claim(AGENT, "demo", booking_id)

    assert claimed.work is not None
    assert claimed.work.request.handling is HandlingState.ASSIGNED
    assert claimed.work.request.booking_id == booking_id
    assert claimed.work.request.flow == FLOW
    assert claimed.work.request.watchdog_at == SLOT_0900 + LENGTH + timedelta(hours=24)
    assert claimed.work.item is not None
    assert claimed.work.item.lease_until == clock.now() + timedelta(minutes=5)
    assert claimed.join_url is None


async def test_a_flow_without_video_needs_no_meeting_and_shows_no_video_state(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A19: no host, no provider, no pending-video state for request work."""
    request_booking = await book(uow, clock)
    legacy_booking = await book(uow, clock, None)
    at(clock)

    view = await queue.view(AGENT, "demo")

    rows = {row.booking_id: row for row in view.rows}
    assert rows[request_booking].without_video
    assert not rows[legacy_booking].without_video


async def test_a_legacy_booking_makes_no_request_and_keeps_its_meeting(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock, None)
    at(clock)

    claimed = await queue.claim(AGENT, "demo", booking_id)

    assert claimed.work is None
    assert claimed.join_url == "https://v/j"
    assert uow.state.requests == {}
    assert await refused(queue.start(AGENT, "demo", booking_id)) is ErrorCode.NOT_CLAIMED


async def test_a_video_flow_on_a_pool_is_not_request_work(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock, VIDEO_FLOW)
    at(clock)

    claimed = await queue.claim(AGENT, "demo", booking_id)

    assert claimed.work is None
    assert uow.state.requests == {}


async def test_start_and_complete_release_the_unit_once_and_complete_the_booking(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock)
    at(clock)
    await queue.claim(AGENT, "demo", booking_id)
    at(clock, 2)

    started = await queue.start(AGENT, "demo", booking_id)

    assert started.work is not None
    assert started.work.item is not None
    assert started.work.item.state is WorkItemState.IN_PROGRESS
    assert started.work.item.pool_id == POOL
    assert started.work.item.planned_until == clock.now() + LENGTH + timedelta(minutes=5)
    async with uow.read() as tx:
        held = await tx.get_pool_commitments(POOL, SLOT_0900, SLOT_0900 + timedelta(hours=1))
    assert [c.in_progress for c in held] == [False, True]  # the hold, then its occupation

    await queue.complete(AGENT, "demo", booking_id)

    async with uow.read() as tx:
        after = await tx.get_pool_commitments(POOL, SLOT_0900, SLOT_0900 + timedelta(hours=1))
        request = await tx.get_request_of_booking(booking_id)
    assert after == []  # completed: the booking holds no place and the occupation ended
    assert request is not None
    assert request.handling is HandlingState.COMPLETED
    assert uow.state.bookings[booking_id].status is BookingStatus.COMPLETED
    assert STARTED in details(uow)
    assert COMPLETED in details(uow)


async def test_only_the_holder_starts_and_completes(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock)
    at(clock)
    await queue.claim(AGENT, "demo", booking_id)

    assert await refused(queue.start(OTHER, "demo", booking_id)) is ErrorCode.NOT_CLAIMED
    await queue.start(AGENT, "demo", booking_id)
    assert await refused(queue.complete(OTHER, "demo", booking_id)) is ErrorCode.NOT_CLAIMED
    assert await refused(queue.complete(ADMIN, "demo", booking_id)) is ErrorCode.NOT_CLAIMED


async def test_a_start_after_the_lease_is_refused(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock)
    at(clock)
    await queue.claim(AGENT, "demo", booking_id)
    at(clock, 5)

    assert await refused(queue.start(AGENT, "demo", booking_id)) is ErrorCode.LEASE_EXPIRED


async def test_a_start_into_units_reserved_for_others_is_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A35: the start is judged on the live staffing; one unit, taken by a running overrun."""
    queue = queue_of(uow, clock)
    first = await book(uow, clock)
    second = await book(uow, clock)
    at(clock)
    await queue.claim(AGENT, "demo", first)
    await queue.start(AGENT, "demo", first)
    uow.state.pool_rules[1] = (POOL, replace(unit_block(0), id=1))  # staffing drops to nobody
    await queue.claim(OTHER, "demo", second)

    assert await refused(queue.start(OTHER, "demo", second)) is ErrorCode.START_REFUSED


async def test_a_due_appointment_starts_when_a_colleague_holds_the_last_unit_and_it_is_recorded(
    clock: FixedClock,
) -> None:
    """ADR 0122 decision 11 (P8): one unit, held by a colleague's running treatment."""
    uow = world(units=1)
    queue = queue_of(uow, clock)
    first = await book(uow, clock)
    second = await book(uow, clock)
    at(clock)
    await queue.claim(AGENT, "demo", first)
    await queue.start(AGENT, "demo", first)
    await queue.claim(OTHER, "demo", second)

    started = await queue.start(OTHER, "demo", second)

    assert started.work is not None
    assert started.work.item is not None
    assert started.work.item.start_conflict
    assert STARTED_IN_CONFLICT in details(uow)


async def test_unstarted_work_is_released_and_started_work_is_not(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock)
    at(clock)
    await queue.claim(AGENT, "demo", booking_id)

    await queue.release(AGENT, "demo", booking_id)

    async with uow.read() as tx:
        request = await tx.get_request_of_booking(booking_id)
    assert request is not None
    assert request.handling is HandlingState.QUEUED
    again = await queue.claim(AGENT, "demo", booking_id)
    assert again.work is not None
    assert again.work.request.id == request.id  # one appointment, one request (A15)
    await queue.start(AGENT, "demo", booking_id)
    assert await refused(queue.release(AGENT, "demo", booking_id)) is (
        ErrorCode.REQUEST_IN_PROGRESS
    )


async def test_completing_unstarted_work_is_refused(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock)
    at(clock)
    await queue.claim(AGENT, "demo", booking_id)

    assert await refused(queue.complete(AGENT, "demo", booking_id)) is (
        ErrorCode.REQUEST_NOT_STARTED
    )


async def test_a_supervisor_completes_work_in_recovery(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock)
    at(clock)
    await queue.claim(AGENT, "demo", booking_id)
    await queue.start(AGENT, "demo", booking_id)
    request = next(iter(uow.state.requests.values()))
    uow.state.requests[request.id or 0] = replace(request, handling=HandlingState.RECOVERY_REQUIRED)

    await queue.complete(ADMIN, "demo", booking_id)

    assert uow.state.requests[request.id or 0].handling is HandlingState.COMPLETED
