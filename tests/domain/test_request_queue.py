"""Immediate work and check-in in the own queue, on the pool of appointments (ADR 0121 step 7).

The staff side: ``RequestQueue`` (claim, start, complete, release of requests without an
appointment) and ``QueueService.check_in``. The brief's example runs end to end (ADR 0122
section 2.4): two units, two appointments at 10:00, a request at 09:55 whose claim is refused
until 10:30. The gate is opened for every test here.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.service_requests import HandlingState
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.immediate import (
    ADMIN,
    AGENT,
    COLLEAGUE,
    DESK_FLOW,
    NOW_FLOW,
    POOL,
    appointment,
    ask,
    at,
    intake,
    queue,
    staff,
    watch,
    world,
)
from tests.support.pool_flows import open_gate
from tests.support.service_flows import skill


@pytest.fixture(autouse=True)
def _gate(monkeypatch: pytest.MonkeyPatch) -> None:
    open_gate(monkeypatch)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return world()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(at(9, 55))


async def two_reservations(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    """R1 and R2: appointments of 30 minutes at 10:00, both units of the pool."""
    for _ in range(2):
        await appointment(uow, clock, at(10), NOW_FLOW, 30)


async def _held(uow: InMemoryUnitOfWork) -> int:
    """Return how many units of the pool immediate work holds now (claimed or started)."""
    return sum(
        1
        for item in uow.state.work_items.values()
        if item.pool_id == POOL and item.ended_at is None
    )


# --- immediate work --------------------------------------------------------------------


async def test_immediate_work_never_takes_a_reserved_unit(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A17 end to end: refused at 09:55, allowed at 10:30 when the reservations end."""
    await two_reservations(uow, clock)
    await ask(uow, clock)
    agents = staff(uow, clock)

    with pytest.raises(DomainError) as error:
        await agents.claim(AGENT, "demo")
    assert error.value.code is ErrorCode.START_REFUSED

    clock.advance(at(10, 30) - clock.now())
    claimed = await agents.claim(AGENT, "demo")
    assert claimed.request.handling is HandlingState.ASSIGNED
    assert claimed.first_name == "Ada"


async def test_a_claim_acquires_the_unit_that_appointments_and_claims_then_see(
    clock: FixedClock,
) -> None:
    """A11: one pool of one unit, one count, whatever path the work came in by."""
    uow = world(units=1)
    first = await ask(uow, clock)
    await ask(uow, clock, key="m" * 43)
    agents = staff(uow, clock)
    await agents.claim(AGENT, "demo")

    with pytest.raises(DomainError) as error:
        await agents.claim(COLLEAGUE, "demo")
    assert error.value.code is ErrorCode.START_REFUSED
    async with uow.read() as tx:
        commitments = await tx.get_pool_commitments(POOL, at(10), at(10, 20))
    assert [c.key for c in commitments] == [("request", first.request.id)]  # what booking sees
    offer = await intake(uow, clock).offer("demo")
    assert offer is not None
    assert offer.decision.expected_start == at(10, 15)  # the claimed unit is busy until then


async def test_start_complete_and_release_count_the_unit_once(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    accepted = await ask(uow, clock)
    agents = staff(uow, clock)
    request_id = accepted.request.id or 0
    await agents.claim(AGENT, "demo")
    await agents.release(AGENT, "demo", request_id)
    assert await _held(uow) == 0

    await agents.claim(AGENT, "demo")
    clock.advance(timedelta(minutes=2))
    started = await agents.start(AGENT, "demo", request_id)
    assert started.item is not None
    assert started.item.started_at == at(9, 57)
    assert started.item.planned_until == at(10, 17)
    assert await _held(uow) == 1
    with pytest.raises(DomainError) as error:
        await agents.release(AGENT, "demo", request_id)
    assert error.value.code is ErrorCode.REQUEST_IN_PROGRESS

    await agents.complete(AGENT, "demo", request_id)
    assert uow.state.requests[request_id].handling is HandlingState.COMPLETED
    assert await _held(uow) == 0


async def test_only_the_holder_starts_and_another_service_has_no_such_request(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    accepted = await ask(uow, clock)
    agents = staff(uow, clock)
    request_id = accepted.request.id or 0
    await agents.claim(AGENT, "demo", request_id)

    with pytest.raises(DomainError) as error:
        await agents.start(COLLEAGUE, "demo", request_id)
    assert error.value.code is ErrorCode.NOT_CLAIMED
    with pytest.raises(DomainError) as error:
        await agents.claimed(AGENT, "demo", 999)
    assert error.value.code is ErrorCode.REQUEST_NOT_FOUND
    await agents.release(ADMIN, "demo", request_id)  # a supervisor may release any claim
    assert uow.state.requests[request_id].handling is HandlingState.QUEUED


async def test_a_missing_skill_keeps_the_request_visible_and_unclaimable(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """Choice S3 and A08 for immediate work."""

    uow.state.skills[3] = replace(skill("passport"), id=3)
    revision = replace(uow.state.flow_revisions[(60, 1)], required_skill_ids=frozenset({3}))
    uow.state.flow_revisions[(60, 1)] = revision
    uow.state.flows[60] = replace(uow.state.flows[60], current=revision)
    await ask(uow, clock)
    agents = staff(uow, clock)

    rows = await agents.view(AGENT, "demo")
    assert [(r.eligible, [s.code for s in r.missing]) for r in rows] == [(False, ["passport"])]
    with pytest.raises(DomainError) as error:
        await agents.claim(AGENT, "demo")
    assert error.value.code is ErrorCode.NOT_ELIGIBLE


async def test_an_expired_lease_releases_the_unit_and_a_late_start_is_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A35 for immediate work."""
    accepted = await ask(uow, clock)
    agents = staff(uow, clock)
    await agents.claim(AGENT, "demo")
    clock.advance(timedelta(minutes=6))

    with pytest.raises(DomainError) as error:
        await agents.start(AGENT, "demo", accepted.request.id or 0)
    assert error.value.code is ErrorCode.LEASE_EXPIRED
    await watch(uow, clock).run()
    assert uow.state.requests[accepted.request.id or 0].handling is HandlingState.QUEUED
    assert await _held(uow) == 0


# --- check-in ---------------------------------------------------------------------------------


async def test_an_in_person_appointment_is_claimed_only_after_its_check_in(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """I2 and A15: two check-ins, one request, one work item, the reservation counted once."""
    booking_id = await appointment(uow, clock, at(10), DESK_FLOW, 30)
    clock.advance(at(10) - clock.now())
    appointments = queue(uow, clock)

    view = await appointments.view(AGENT, "demo")
    assert [r.awaiting_check_in for r in view.rows] == [True]
    assert view.next_booking_id is None
    with pytest.raises(DomainError) as error:
        await appointments.claim(AGENT, "demo", booking_id)
    assert error.value.code is ErrorCode.NOT_CHECKED_IN

    await appointments.check_in(AGENT, "demo", booking_id)
    await appointments.check_in(COLLEAGUE, "demo", booking_id)
    claimed = await appointments.claim_next(AGENT, "demo")
    await appointments.start(AGENT, "demo", booking_id)

    assert claimed.booking_id == booking_id
    assert len(uow.state.requests) == 1
    assert len(uow.state.work_items) == 1
    assert next(iter(uow.state.requests.values())).arrived_at == at(10)
    async with uow.read() as tx:
        held = await tx.get_pool_commitments(POOL, at(10), at(10, 30))
    assert len({c.key for c in held}) == 1
    checked = [e for e in uow.state.audit if e.detail == "queue.checked_in"]
    assert len(checked) == 1


async def test_a_check_in_outside_the_queues_window_is_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await appointment(uow, clock, at(11, 30), DESK_FLOW, 30)
    clock.advance(at(10, 20) - clock.now())  # 70 minutes before: not shown yet
    with pytest.raises(DomainError) as error:
        await queue(uow, clock).check_in(AGENT, "demo", booking_id)
    assert error.value.code is ErrorCode.OUTSIDE_BOOKING_WINDOW
