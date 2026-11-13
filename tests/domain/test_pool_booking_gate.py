"""The rollback gate ``ROLLBACK_SAFE`` keeps booking on a pool dark (ADR 0122 step 5).

With the gate shut, a service with an active flow on a staffed counted pool behaves exactly as
the same service without it: the same times, the same bookings, the same slots, and no pool slot
anywhere. The pool flow is not offered, cannot be named and is never a default. The readiness
checklist reports ``rollback_not_safe``, so no flow on a pool can be activated either.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_readiness import (
    FOLLOWING,
    ReadinessCheck,
    ReadinessReason,
    ReadinessState,
    can_activate,
    can_publish,
    readiness,
    readiness_state,
)
from app.domain.flow_resolution import gate_allows, pool_flows_bookable
from app.domain.scheduling import Organisation, ScheduleRule
from app.domain.service_flows import (
    FlowState,
    IntakeMode,
)
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import (
    MONDAY,
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    make_group,
    participant,
    window,
)
from tests.support.pool_flows import add_counted_pool, add_pool_flow, block, open_gate
from tests.support.service_flows import flow, legacy, pool, revision

GATES = [pytest.param(False, id="gate-shut"), pytest.param(True, id="gate-open")]


def _state() -> State:
    state = State()
    state.organisations = {1: Organisation(1, "Acme", "acme")}
    state.organisation_slugs = {1: "acme"}
    state.groups[1] = make_group(id=1, slug="balie")
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    state.next_id = 100
    return state


async def _uow(with_pool_flow: bool) -> InMemoryUnitOfWork:
    """Return the service with its legacy flows, and on request a staffed pool and a pool flow.

    The pool is staffed over the very times of the service's own staffing, so any leak of the
    pool into the service's paths would show.
    """
    uow = InMemoryUnitOfWork(_state())
    async with uow.locked() as tx:
        await tx.ensure_default_flows(1)
    if with_pool_flow:
        pool_id = await add_counted_pool(uow, 1, [block(units=5)])
        await add_pool_flow(uow, 1, pool_id, duration=30)
    return uow


def _service(uow: InMemoryUnitOfWork) -> BookingService:
    return BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=15)


async def _trace(uow: InMemoryUnitOfWork) -> list[Any]:
    """Run every participant path of the service once and return what is observable."""
    service = _service(uow)
    seen: list[Any] = [await service.availability("balie", MONDAY, MONDAY)]
    seen.append(await service.availability("balie", MONDAY, MONDAY, flow="appointment"))
    first = await service.book(
        group_slug="balie", start=SLOT_0900, contact=participant(1), locale="nl"
    )
    await service.confirm(first.management_token)
    second = await service.book(
        group_slug="balie", start=SLOT_0900, contact=participant(2), locale="nl", flow="appointment"
    )
    with pytest.raises(DomainError) as full:
        await service.book(group_slug="balie", start=SLOT_0900, contact=participant(3), locale="nl")
    seen.append(full.value.code)
    moved = await service.reschedule(first.management_token, SLOT_0930)
    seen.append(await service.suggestions(moved.management_token))
    seen.append(await service.move_times(moved.booking, MONDAY, MONDAY))
    await service.cancel(second.management_token)
    seen.append(await service.availability("balie", MONDAY, MONDAY))
    seen.append(
        sorted(
            (s.start, s.end, s.capacity_snapshot, s.kind, s.capacity_pool_id, s.occupied_until)
            for s in uow.state.slots.values()
        )
    )
    seen.append(
        sorted(
            (b.start, b.end, b.status, b.flow, b.rescheduled_from_id is not None)
            for b in uow.state.bookings.values()
        )
    )
    seen.append(
        sorted(
            (job.type, job.run_at)
            for booking_id, jobs in uow.state.jobs.items()
            for job in jobs
            if booking_id in uow.state.bookings
        )
    )
    return seen


@pytest.mark.parametrize("gate_open", GATES)
async def test_every_path_of_the_service_is_the_same_with_a_pool_flow_beside_it(
    gate_open: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proof that a pool flow next to a service changes none of the service's own paths."""
    if gate_open:
        open_gate(monkeypatch)
    plain, pooled = await _uow(False), await _uow(True)

    assert await _trace(pooled) == await _trace(plain)
    assert not [s for s in pooled.state.slots.values() if s.capacity_pool_id is not None]


async def test_with_the_gate_shut_the_pool_flow_cannot_be_named_and_nothing_is_stored() -> None:
    uow = await _uow(True)
    service = _service(uow)
    assert pool_flows_bookable(IntakeMode.APPOINTMENT) is False

    for call in (
        service.flow_for("balie", IntakeMode.APPOINTMENT, "advies"),
        service.book(
            group_slug="balie", start=SLOT_0900, contact=participant(1), locale="nl", flow="advies"
        ),
    ):
        with pytest.raises(DomainError) as error:
            await call
        assert error.value.code is ErrorCode.FLOW_NOT_AVAILABLE
    assert not uow.state.bookings
    assert not uow.state.slots


async def test_with_the_gate_shut_availability_reads_no_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uow = await _uow(True)
    plain = await _service(await _uow(False)).availability("balie", MONDAY, MONDAY)

    async def forbidden(*args: object) -> None:
        raise AssertionError("no flow is read for availability while the gate is shut")

    monkeypatch.setattr("app.domain.services.resolve_flow", forbidden)

    for code in (None, "advies", "appointment"):
        found = await _service(uow).availability("balie", MONDAY, MONDAY, flow=code)
        assert found == plain


async def test_a_pool_flow_made_the_default_is_ignored_while_the_gate_is_shut() -> None:
    """ADR 0121 point 8: without an active default the booking is made on the service's own
    staffing and records no flow; the pool is never reached."""
    uow = await _uow(False)
    for flow_id, stored in list(uow.state.flows.items()):
        if stored.code == "appointment":
            uow.state.flows[flow_id] = replace(stored, state=FlowState.ARCHIVED)
    pool_id = await add_counted_pool(uow, 1, [block(units=5)])
    await add_pool_flow(uow, 1, pool_id, duration=30, default=True)

    receipt = await _service(uow).book(
        group_slug="balie", start=SLOT_0900, contact=participant(1), locale="nl"
    )

    assert receipt.booking.flow is None
    assert uow.state.slots[receipt.booking.slot_id].capacity_pool_id is None


async def test_the_same_default_books_on_the_pool_once_the_gate_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_gate(monkeypatch)
    uow = await _uow(False)
    for flow_id, stored in list(uow.state.flows.items()):
        if stored.code == "appointment":
            uow.state.flows[flow_id] = replace(stored, state=FlowState.ARCHIVED)
    pool_id = await add_counted_pool(uow, 1, [block(units=5)])
    made = await add_pool_flow(uow, 1, pool_id, duration=30, default=True)
    start = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)  # 10:30 local: a pool time only (step 45)

    times = await _service(uow).availability("balie", MONDAY, MONDAY)
    receipt = await _service(uow).book(
        group_slug="balie", start=start, contact=participant(1), locale="nl"
    )

    assert start in [s.start for s in times]
    assert receipt.booking.flow is not None
    assert receipt.booking.flow.flow_id == made.flow_id
    assert uow.state.slots[receipt.booking.slot_id].capacity_pool_id == pool_id


@pytest.mark.parametrize("gate_open", GATES)
def test_the_gate_lets_a_pool_flow_through_on_both_paths(
    gate_open: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Appointments since ADR 0122 step 5, immediate intake since ADR 0121 step 7."""
    if gate_open:
        open_gate(monkeypatch)
    both = frozenset({IntakeMode.APPOINTMENT, IntakeMode.ON_DEMAND})
    on_pool = flow("advies", current=revision(intake_modes=both, published_at=NOW), flow_id=1)
    wrapper = flow("appointment", current=legacy(published_at=NOW), flow_id=2)

    assert gate_allows(on_pool, IntakeMode.APPOINTMENT) is gate_open
    assert gate_allows(on_pool, IntakeMode.ON_DEMAND) is gate_open
    assert gate_allows(wrapper, IntakeMode.APPOINTMENT) is True
    assert gate_allows(wrapper, IntakeMode.ON_DEMAND) is True


# --- the readiness checklist reads the gate ---------------------------------------------------


def test_a_new_flow_waits_for_the_gate_on_its_own_point() -> None:
    items = readiness(revision(), pool(), offered_subjects=())

    retention = next(i for i in items if i.code is ReadinessCheck.RETENTION)
    rollback = next(i for i in items if i.code is ReadinessCheck.ROLLBACK)
    assert retention.ok
    assert rollback.reason is ReadinessReason.ROLLBACK_NOT_SAFE
    assert not can_activate(items)
    assert readiness_state(items) is ReadinessState.COMPLETE_EXCEPT_FOLLOWING
    assert can_publish(items)  # publishing does not activate


def test_an_open_gate_makes_a_new_flow_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    open_gate(monkeypatch)

    items = readiness(revision(), pool(), offered_subjects=())

    assert can_activate(items)


def test_the_legacy_wrapper_never_waits_for_the_gate() -> None:
    assert can_activate(readiness(legacy(), pool(), offered_subjects=()))


def test_the_following_reasons_are_exactly_the_two_an_administrator_cannot_fix() -> None:
    assert {ReadinessReason.NOT_BUILT_YET, ReadinessReason.ROLLBACK_NOT_SAFE} == FOLLOWING
