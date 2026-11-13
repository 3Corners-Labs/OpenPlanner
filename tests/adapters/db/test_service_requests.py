"""Requests, work items and the request-level record: one set of cases for SQL and the fake.

ADR 0121 step 5. Each test runs on ``SqlUnitOfWork`` over a migrated database and on
``InMemoryUnitOfWork`` (the ``world`` fixture), so the fake cannot drift from the adapter. The
lifecycle rules are ``app.domain.service_requests`` and are tested in the domain; these tests pin
what the backends store: one request per booking (A15), one active work item per request, the
conditional updates, the occupation of a started work item in the pool's commitments (counted
once with the booking's hold, kept in recovery, released once) and the deletion of the
request-level record and of the agent pseudonyms (decision D1).
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef
from app.domain.pool_accounting import Commitment
from app.domain.service_flows import IntakeMode
from app.domain.service_requests import (
    HandlingState,
    PersonalDataState,
    RequestPersonalData,
    ServiceRequest,
    WorkItem,
    WorkItemState,
    claim,
    complete,
    start,
    watchdog_deadline,
    watchdog_outcome,
)
from tests.support.domain import NOW, SLOT_0900
from tests.support.flow_world import EVERYTHING, World
from tests.support.pool_flows import PoolFlow
from tests.support.request_storage import LENGTH, UNTIL, pool_booking, request_for

AGENT = "agent-pseudonym"


async def claimed(world: World) -> tuple[PoolFlow, ServiceRequest, WorkItem]:
    """Store a pool booking's request and its claimed work item."""
    flow, booking_id = await pool_booking(world)
    async with world.uow.locked() as tx:
        request = await tx.add_service_request(request_for(world, flow, booking_id))
        assigned, item = claim(request, None, now=SLOT_0900, agent=AGENT, lease_minutes=5)
        assert await tx.update_service_request(assigned, HandlingState.QUEUED)
        stored = await tx.add_work_item(item)
    return flow, assigned, stored


async def test_a_request_round_trips_and_is_found_by_its_booking(world: World) -> None:
    flow, booking_id = await pool_booking(world)
    wanted = request_for(world, flow, booking_id)

    async with world.uow.locked() as tx:
        stored = await tx.add_service_request(wanted)
    async with world.uow.read() as tx:
        found = await tx.get_request_of_booking(booking_id)
        none = await tx.get_request_of_booking(booking_id + 100)

    assert stored.id is not None
    assert found == stored == replace(wanted, id=stored.id)
    assert found.watchdog_at == SLOT_0900 + LENGTH + timedelta(hours=24)
    assert none is None


async def test_one_appointment_never_gets_two_requests(world: World) -> None:
    flow, booking_id = await pool_booking(world)
    async with world.uow.locked() as tx:
        await tx.add_service_request(request_for(world, flow, booking_id))

    with pytest.raises(Exception, match="UNIQUE"):  # noqa: PT011 - the database's own message
        async with world.uow.locked() as tx:
            await tx.add_service_request(request_for(world, flow, booking_id))


async def test_a_record_on_a_request_with_a_booking_is_refused(world: World) -> None:
    flow, booking_id = await pool_booking(world)

    with pytest.raises(DomainError) as refused:
        async with world.uow.locked() as tx:
            await tx.add_service_request(
                request_for(world, flow, booking_id), RequestPersonalData(first_name="Ada")
            )

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
    assert refused.value.field == "data"


async def test_one_active_work_item_per_request(world: World) -> None:
    _, _, item = await claimed(world)
    async with world.uow.read() as tx:
        assert await tx.get_active_work_item(item.request_id) == item

    with pytest.raises(Exception, match="UNIQUE"):  # noqa: PT011 - the database's own message
        async with world.uow.locked() as tx:
            await tx.add_work_item(replace(item, id=None))


async def test_updates_are_conditional_on_the_state_read(world: World) -> None:
    _, request, item = await claimed(world)
    running_request, running = start(
        request, item, now=SLOT_0900, pool_id=item.request_id, planned_until=UNTIL, conflict=False
    )

    async with world.uow.locked() as tx:
        assert not await tx.update_service_request(running_request, HandlingState.QUEUED)
        assert not await tx.update_work_item(running, WorkItemState.IN_PROGRESS)
        assert await tx.update_service_request(running_request, HandlingState.ASSIGNED)
        assert await tx.update_work_item(running, WorkItemState.ASSIGNED)
    async with world.uow.read() as tx:
        stored = await tx.get_active_work_item(item.request_id)
        again = await tx.get_request_of_booking(request.booking_id or 0)

    assert stored == running
    assert stored.assigned_to == AGENT
    assert again is not None
    assert again.handling is HandlingState.IN_PROGRESS


async def started(world: World) -> tuple[PoolFlow, ServiceRequest, WorkItem]:
    """Store a claimed request and start it on its flow's pool at 09:00."""
    flow, request, item = await claimed(world)
    running_request, running = start(
        request, item, now=SLOT_0900, pool_id=flow.pool_id, planned_until=UNTIL, conflict=False
    )
    async with world.uow.locked() as tx:
        assert await tx.update_service_request(running_request, HandlingState.ASSIGNED)
        assert await tx.update_work_item(running, WorkItemState.ASSIGNED)
    return flow, running_request, running


async def commitments(world: World, pool_id: int, hours: int = 1) -> Any:
    """Return the pool's commitments from 09:00 for ``hours``."""
    async with world.uow.read() as tx:
        return await tx.get_pool_commitments(pool_id, SLOT_0900, SLOT_0900 + timedelta(hours=hours))


async def test_a_started_work_item_occupies_the_unit_its_booking_holds(world: World) -> None:
    flow, request, _ = await started(world)

    found = await commitments(world, flow.pool_id)

    hold = Commitment(("booking", request.booking_id or 0), SLOT_0900, UNTIL)
    occupied = Commitment(hold.key, SLOT_0900, UNTIL, in_progress=True)
    assert found == [hold, occupied]
    assert found[-1].in_progress  # the occupation comes last, so one_per_key keeps it


async def test_an_overrun_stays_a_commitment_after_its_planned_end(world: World) -> None:
    flow, request, _ = await started(world)

    async with world.uow.read() as tx:
        later = await tx.get_pool_commitments(
            flow.pool_id, UNTIL + timedelta(hours=1), UNTIL + timedelta(hours=2)
        )

    assert later == [
        Commitment(("booking", request.booking_id or 0), SLOT_0900, UNTIL, in_progress=True)
    ]


async def test_recovery_keeps_the_unit_and_completion_releases_it(world: World) -> None:
    flow, request, item = await started(world)
    outcome = watchdog_outcome(request, item, now=request.watchdog_at)
    assert outcome is not None
    assert outcome.item is not None
    async with world.uow.locked() as tx:
        assert await tx.update_service_request(outcome.request, HandlingState.IN_PROGRESS)
        assert await tx.update_work_item(outcome.item, WorkItemState.IN_PROGRESS)
    assert [c.in_progress for c in await commitments(world, flow.pool_id)] == [False, True]

    done, ended = complete(outcome.request, outcome.item, now=request.watchdog_at)
    async with world.uow.locked() as tx:
        assert await tx.update_service_request(done, HandlingState.RECOVERY_REQUIRED)
        assert await tx.update_work_item(ended, WorkItemState.RECOVERY_REQUIRED)
        assert not await tx.update_work_item(ended, WorkItemState.RECOVERY_REQUIRED)  # once
        assert await tx.get_active_work_item(item.request_id) is None

    assert [c.in_progress for c in await commitments(world, flow.pool_id)] == [False]


async def test_the_booking_purge_empties_the_agent_pseudonym_and_keeps_the_states(
    world: World,
) -> None:
    _, request, item = await claimed(world)

    async with world.uow.locked() as tx:
        await tx.purge_personal_data(request.booking_id or 0, NOW)
    async with world.uow.read() as tx:
        found = await tx.get_active_work_item(item.request_id)
        kept = await tx.get_request_of_booking(request.booking_id or 0)

    assert found is not None
    assert found.assigned_to is None
    assert found.state is WorkItemState.ASSIGNED
    assert kept == request


LATER = NOW + timedelta(minutes=35)
"""The end of the unit an immediate claim acquires: 30 minutes plus a 5-minute wrap-up."""


def on_demand(world: World, flow: PoolFlow) -> ServiceRequest:
    """Return a request without an appointment of Acme's service (the writer is step 7)."""
    return ServiceRequest(
        id=None,
        organisation_id=world.acme,
        group_id=world.acme_group,
        flow=FlowRef(flow.flow_id, 1),
        intake=IntakeMode.ON_DEMAND,
        booking_id=None,
        handling=HandlingState.QUEUED,
        created_at=NOW,
        watchdog_at=watchdog_deadline(NOW, 24),
        pool_id=flow.pool_id,
    )


async def test_the_record_is_stored_read_and_deleted_once(world: World) -> None:
    flow, _ = await pool_booking(world)
    record = RequestPersonalData(first_name="Ada", email="ada@voorbeeld.nl")
    async with world.uow.locked() as tx:
        request = await tx.add_service_request(on_demand(world, flow), record)
        item = await tx.add_work_item(
            claim(request, None, now=NOW, agent=AGENT, lease_minutes=5, planned_until=LATER)[1]
        )
    assert request.personal_data is PersonalDataState.PRESENT
    async with world.uow.read() as tx:
        assert await tx.get_request_personal_data(request.id or 0) == record

    async with world.uow.locked() as tx:
        assert await tx.delete_request_personal_data(request.id or 0, NOW)
        assert not await tx.delete_request_personal_data(request.id or 0, NOW)
    async with world.uow.read() as tx:
        gone = await tx.get_request_personal_data(request.id or 0)
        worked = await tx.get_active_work_item(request.id or 0)

    assert gone is None
    assert worked is not None
    assert worked.id == item.id
    assert worked.assigned_to is None


async def test_nothing_collected_stores_no_record(world: World) -> None:
    flow, _ = await pool_booking(world)

    async with world.uow.locked() as tx:
        request = await tx.add_service_request(on_demand(world, flow), RequestPersonalData())
        empty = await tx.get_request_personal_data(request.id or 0)

    assert request.personal_data is PersonalDataState.NOT_COLLECTED
    assert empty is None


async def test_the_watchdog_reads_open_requests_unclaimed_pool_bookings_and_held_records(
    world: World,
) -> None:
    flow, booking_id = await pool_booking(world)
    later = SLOT_0900 + timedelta(days=2)
    async with world.uow.read() as tx:
        unclaimed = await tx.unclaimed_pool_bookings(later)
        too_early = await tx.unclaimed_pool_bookings(SLOT_0900)
    assert [(u.booking_id, u.organisation_id, u.flow) for u in unclaimed] == [
        (booking_id, world.acme, FlowRef(flow.flow_id, 1))
    ]
    assert unclaimed[0].end == SLOT_0900 + LENGTH
    assert too_early == []

    async with world.uow.locked() as tx:
        request = await tx.add_service_request(request_for(world, flow, booking_id))
        held = await tx.add_service_request(
            on_demand(world, flow), RequestPersonalData(first_name="Ada")
        )
        done = await tx.add_service_request(
            replace(on_demand(world, flow), handling=HandlingState.EXPIRED, resolved_at=NOW)
        )
    async with world.uow.read() as tx:
        assert await tx.unclaimed_pool_bookings(later) == []
        assert await tx.open_request_ids() == [request.id, held.id]
        assert [r.id for r in await tx.requests_holding_data()] == [held.id]
        assert await tx.get_service_request(done.id or 0) == done
        assert await tx.get_service_request((done.id or 0) + 100) is None


async def test_a_pseudonym_without_a_record_is_held_data_and_is_emptied_once(
    world: World,
) -> None:
    """Review finding 1: the pseudonym goes even when the flow collected no record."""
    flow, booking_id = await pool_booking(world)
    async with world.uow.locked() as tx:
        appointment = await tx.add_service_request(request_for(world, flow, booking_id))
        await tx.add_work_item(claim(appointment, None, now=NOW, agent=AGENT, lease_minutes=5)[1])
        request = await tx.add_service_request(on_demand(world, flow))
        await tx.add_work_item(
            claim(request, None, now=NOW, agent=AGENT, lease_minutes=5, planned_until=LATER)[1]
        )
    async with world.uow.read() as tx:
        held = [r.id for r in await tx.requests_holding_data()]
    assert held == [request.id]  # an appointment's pseudonym goes with its booking's purge

    async with world.uow.locked() as tx:
        assert await tx.delete_request_personal_data(request.id or 0, NOW)
        assert not await tx.delete_request_personal_data(request.id or 0, NOW)
    async with world.uow.read() as tx:
        item = await tx.get_active_work_item(request.id or 0)
        kept = await tx.get_service_request(request.id or 0)
        assert await tx.requests_holding_data() == []
    assert item is not None
    assert item.assigned_to is None
    assert kept is not None
    assert kept.personal_data is PersonalDataState.NOT_COLLECTED


async def test_the_watchdog_moment_is_written_with_the_handling(world: World) -> None:
    flow, booking_id = await pool_booking(world)
    async with world.uow.locked() as tx:
        request = await tx.add_service_request(request_for(world, flow, booking_id))
        later = replace(request, watchdog_at=request.watchdog_at + timedelta(days=7))
        assert await tx.update_service_request(later, HandlingState.QUEUED)
    async with world.uow.read() as tx:
        assert await tx.get_service_request(request.id or 0) == later


# --- ADR 0121 step 7: immediate work ----------------------------------------------------------


def immediate(world: World, flow: PoolFlow, ref: str, key: str) -> ServiceRequest:
    """Return immediate work of Acme's service with its access hashes."""
    return replace(
        on_demand(world, flow), public_ref=ref, access_hash=f"a-{ref}", intake_key_hash=key
    )


async def test_immediate_work_is_found_by_reference_key_line_and_service(world: World) -> None:
    flow, _ = await pool_booking(world)
    async with world.uow.locked() as tx:
        first = await tx.add_service_request(immediate(world, flow, "r1", "k1"))
        second = await tx.add_service_request(immediate(world, flow, "r2", "k2"))
        assigned, _ = claim(
            second, None, now=NOW, agent=AGENT, lease_minutes=5, planned_until=LATER
        )
        assert await tx.update_service_request(assigned, HandlingState.QUEUED)
        assert await tx.set_request_access(first.id or 0, "a-new")
    async with world.uow.read() as tx:
        by_ref = await tx.get_request_by_ref("r1")
        by_key = await tx.get_request_by_intake_key(world.acme_group, "k2")
        other_service = await tx.get_request_by_intake_key(world.acme_group + 99, "k2")
        line = await tx.waiting_immediate(flow.pool_id)
        open_ = await tx.open_immediate(world.acme_group)

    assert by_ref is not None
    assert by_ref.access_hash == "a-new"
    assert by_key is not None
    assert by_key.id == second.id
    assert other_service is None
    assert [r.id for r in line] == [first.id, second.id]
    assert [r.id for r in open_] == [first.id, second.id]


async def test_an_intake_key_makes_one_request_per_service(world: World) -> None:
    flow, _ = await pool_booking(world)
    async with world.uow.locked() as tx:
        await tx.add_service_request(immediate(world, flow, "r1", "k1"))
    with pytest.raises(Exception, match="UNIQUE"):  # noqa: PT011 - the database's own message
        async with world.uow.locked() as tx:
            await tx.add_service_request(immediate(world, flow, "r2", "k1"))


async def test_a_claimed_immediate_request_holds_its_unit_until_its_item_ends(
    world: World,
) -> None:
    flow, _ = await pool_booking(world)
    async with world.uow.locked() as tx:
        request = await tx.add_service_request(immediate(world, flow, "r1", "k1"))
        assigned, item = claim(
            request, None, now=NOW, agent=AGENT, lease_minutes=5, planned_until=LATER
        )
        await tx.update_service_request(assigned, HandlingState.QUEUED)
        stored = await tx.add_work_item(item)
    key = ("request", request.id or 0)
    async with world.uow.read() as tx:
        held = await tx.get_pool_commitments(flow.pool_id, NOW, LATER)
    assert Commitment(key, NOW, LATER) in held

    async with world.uow.locked() as tx:
        ended = replace(stored, state=WorkItemState.CANCELLED, lease_until=None, ended_at=NOW)
        assert await tx.update_work_item(ended, WorkItemState.ASSIGNED)
    async with world.uow.read() as tx:
        held = await tx.get_pool_commitments(flow.pool_id, NOW, LATER)
    assert all(c.key != key for c in held)


async def test_the_check_in_is_written_with_the_handling(world: World) -> None:
    flow, booking_id = await pool_booking(world)
    async with world.uow.locked() as tx:
        request = await tx.add_service_request(request_for(world, flow, booking_id))
        arrived = replace(request, arrived_at=NOW)
        assert await tx.update_service_request(arrived, HandlingState.QUEUED)
    async with world.uow.read() as tx:
        found = await tx.get_request_of_booking(booking_id)
    assert found is not None
    assert found.arrived_at == NOW


async def test_the_deletion_also_empties_the_access_hashes(world: World) -> None:
    flow, _ = await pool_booking(world)
    async with world.uow.locked() as tx:
        request = await tx.add_service_request(immediate(world, flow, "r1", "k1"))
        assert await tx.delete_request_personal_data(request.id or 0, NOW)
        assert not await tx.delete_request_personal_data(request.id or 0, NOW)
        assert not await tx.set_request_access(request.id or 0, "again")
    async with world.uow.read() as tx:
        found = await tx.get_service_request(request.id or 0)
    assert found is not None
    assert (found.access_hash, found.intake_key_hash) == (None, None)
    assert found.public_ref == "r1"


async def test_a_pools_waiting_limit_is_bounded_and_scoped(world: World) -> None:
    flow, _ = await pool_booking(world)
    async with world.uow.locked() as tx:
        assert await tx.set_pool_max_waiting(EVERYTHING, flow.pool_id, 12)
        assert not await tx.set_pool_max_waiting(EVERYTHING, flow.pool_id + 99, 12)
        with pytest.raises(DomainError) as error:
            await tx.set_pool_max_waiting(EVERYTHING, flow.pool_id, 1001)
    assert error.value.field == "max_waiting"
    async with world.uow.read() as tx:
        assert await tx.get_pool_max_waiting(flow.pool_id) == 12
    async with world.uow.locked() as tx:
        assert await tx.set_pool_max_waiting(EVERYTHING, flow.pool_id, None)
    async with world.uow.read() as tx:
        assert await tx.get_pool_max_waiting(flow.pool_id) is None
