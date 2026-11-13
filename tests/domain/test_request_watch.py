"""The watchdog round and the deletion of request-level records (ADR 0121 step 5b).

On the in-memory store with the world of ``test_request_work``: lease expiry (back to the
queue), a booking cancelled elsewhere (cancelled), the deadline (expired, or recovery with the
unit kept, A33 and A34), a pool booking nobody claimed (an expired request), and the record of
a request without an appointment deleted on time, by the round and by the nightly sweep.
"""

from dataclasses import replace
from datetime import timedelta

from app.domain.booking import BookingStatus
from app.domain.flow_resolution import FlowRef
from app.domain.request_watch import RequestWatch
from app.domain.retention_sweep import RetentionSweep
from app.domain.service_flows import IntakeMode
from app.domain.service_requests import (
    HandlingState,
    PersonalDataState,
    RequestPersonalData,
    ServiceRequest,
    WorkItemState,
    watchdog_deadline,
)
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW, SLOT_0900
from tests.support.request_work import AGENT, LENGTH, POOL, at, book, queue_of, world

END = SLOT_0900 + LENGTH
DEADLINE = END + timedelta(hours=24)


def watch(uow: InMemoryUnitOfWork, clock: FixedClock) -> RequestWatch:
    """Return the round with 24 watchdog hours."""
    return RequestWatch(uow, clock, 24)


async def test_a_lease_that_ran_out_returns_the_work_to_the_queue() -> None:
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    at(clock)
    await queue_of(uow, clock).claim(AGENT, "demo", booking_id)
    at(clock, 5)

    result = await watch(uow, clock).run()

    request = next(iter(uow.state.requests.values()))
    assert result.changed == 1
    assert request.handling is HandlingState.QUEUED
    assert uow.state.bookings[booking_id].claim is None
    assert [i.state for i in uow.state.work_items.values()] == [WorkItemState.LEASE_EXPIRED]
    assert (await watch(uow, clock).run()).changed == 0


async def test_a_booking_cancelled_elsewhere_cancels_its_unstarted_request() -> None:
    """A previous image cancels the booking; the round sees it (rollback, ADR 0116)."""
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    at(clock)
    await queue_of(uow, clock).claim(AGENT, "demo", booking_id)
    booking = uow.state.bookings[booking_id]
    uow.state.bookings[booking_id] = replace(
        booking, status=BookingStatus.CANCELLED, cancelled_at=clock.now()
    )

    await watch(uow, clock).run()

    request = next(iter(uow.state.requests.values()))
    assert request.handling is HandlingState.CANCELLED
    assert request.resolved_at == clock.now()


async def test_started_work_past_the_deadline_is_marked_for_recovery_and_keeps_its_unit() -> None:
    """A34."""
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    at(clock)
    queue = queue_of(uow, clock)
    await queue.claim(AGENT, "demo", booking_id)
    await queue.start(AGENT, "demo", booking_id)
    clock.advance(DEADLINE - clock.now())

    await watch(uow, clock).run()

    request = next(iter(uow.state.requests.values()))
    assert request.handling is HandlingState.RECOVERY_REQUIRED
    async with uow.read() as tx:
        held = await tx.get_pool_commitments(POOL, clock.now(), clock.now() + timedelta(hours=1))
    assert [c.in_progress for c in held] == [True]  # the overrun still holds its unit
    assert uow.state.bookings[booking_id].claim is not None


async def test_a_pool_booking_nobody_claimed_gets_an_expired_request_once() -> None:
    """A33: never claimed, never checked in: expired at the deadline, then deleted."""
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    clock.advance(DEADLINE - timedelta(minutes=1) - clock.now())
    assert (await watch(uow, clock).run()).expired_unclaimed == 0

    clock.advance(timedelta(minutes=1))
    first = await watch(uow, clock).run()
    second = await watch(uow, clock).run()

    assert (first.expired_unclaimed, second.expired_unclaimed) == (1, 0)
    request = next(iter(uow.state.requests.values()))
    assert request.booking_id == booking_id
    assert request.handling is HandlingState.EXPIRED


async def test_a_legacy_booking_is_never_watched() -> None:
    uow, clock = world(), FixedClock(NOW)
    await book(uow, clock, None)
    clock.advance(DEADLINE + timedelta(days=1) - clock.now())

    await watch(uow, clock).run()

    assert uow.state.requests == {}


def on_demand() -> ServiceRequest:
    """Return a stored-shape request without an appointment of the demo service."""
    return ServiceRequest(
        id=None,
        organisation_id=1,
        group_id=1,
        flow=FlowRef(50, 1),
        intake=IntakeMode.ON_DEMAND,
        booking_id=None,
        handling=HandlingState.QUEUED,
        created_at=NOW,
        watchdog_at=watchdog_deadline(NOW, 24),
    )


async def store_record(uow: InMemoryUnitOfWork) -> int:
    """Store a request without an appointment that holds a name (the writer is step 7)."""
    async with uow.locked() as tx:
        request = await tx.add_service_request(on_demand(), RequestPersonalData(first_name="Ada"))
    return request.id or 0


async def test_the_round_deletes_a_record_at_its_moment_and_not_before() -> None:
    """A20: the request stays valid without data; the record goes a day after the deadline."""
    uow, clock = world(), FixedClock(NOW)
    request_id = await store_record(uow)
    moment = watchdog_deadline(NOW, 24) + timedelta(hours=24)
    clock.advance(moment - timedelta(seconds=1) - NOW)
    assert (await watch(uow, clock).run()).deleted == 0

    clock.advance(timedelta(seconds=1))
    result = await watch(uow, clock).run()

    assert result.deleted == 1
    assert uow.state.request_data == {}
    assert uow.state.requests[request_id].personal_data is PersonalDataState.DELETED


async def test_the_sweep_deletes_an_overdue_record_the_round_missed_and_counts_it() -> None:
    uow, clock = world(), FixedClock(NOW)
    request_id = await store_record(uow)
    clock.advance(timedelta(days=4))

    found = await RetentionSweep(uow, clock).run(dry_run=True)
    result = await RetentionSweep(uow, clock).run()

    assert (found.overdue, found.purged) == (1, 0)
    assert found.backlog == 1
    assert (result.overdue, result.purged, result.failed) == (1, 1, 0)
    assert result.per_group == {"demo": 1}
    assert uow.state.requests[request_id].personal_data is PersonalDataState.DELETED
