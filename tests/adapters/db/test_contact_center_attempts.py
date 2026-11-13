"""Contact-center attempts in storage: one set of cases for SQL and the fake (ADR 0121 step 6b).

The channel round-trips and never changes after the attempt opens; an attempt counts as the
request's active work item, so a claim of the own queue cannot be stored next to it (the fence);
the attempts in doubt are listed for the fence round, oldest first; every work item of a request
is listed in order.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain import contact_center_attempts as attempts
from app.domain.service_requests import (
    HandlingState,
    ServiceRequest,
    WorkChannel,
    WorkItem,
    WorkItemState,
    claim,
)
from tests.support.domain import SLOT_0900
from tests.support.flow_world import World
from tests.support.pool_flows import PoolFlow
from tests.support.request_storage import pool_booking, request_for


async def opened(world: World) -> tuple[PoolFlow, ServiceRequest, WorkItem]:
    """Store a pool booking's request with an open contact-center attempt."""
    flow, booking_id = await pool_booking(world)
    async with world.uow.locked() as tx:
        request = await tx.add_service_request(request_for(world, flow, booking_id))
        assigned, item = attempts.open_attempt(request, None, now=SLOT_0900)
        assert await tx.update_service_request(assigned, HandlingState.QUEUED)
        stored = await tx.add_work_item(item)
    return flow, assigned, stored


async def test_an_attempt_round_trips_with_its_channel(world: World) -> None:
    _, request, item = await opened(world)

    async with world.uow.read() as tx:
        found = await tx.get_active_work_item(request.id or 0)
        listed = await tx.work_items_of(request.id or 0)

    assert found == item
    assert found is not None
    assert (found.channel, found.state) == (WorkChannel.CONTACT_CENTER, WorkItemState.DISPATCHING)
    assert listed == [item]


async def test_an_attempt_fences_out_a_claim_of_the_own_queue(world: World) -> None:
    _, request, _ = await opened(world)
    _, own = claim(
        replace(request, handling=HandlingState.QUEUED),
        None,
        now=SLOT_0900,
        agent="agent",
        lease_minutes=5,
    )

    with pytest.raises(Exception, match="UNIQUE"):  # noqa: PT011 - the database's own message
        async with world.uow.locked() as tx:
            await tx.add_work_item(own)


async def test_the_attempts_in_doubt_are_listed_and_the_channel_is_kept(world: World) -> None:
    _, request, item = await opened(world)
    doubt = attempts.in_doubt(item)
    async with world.uow.locked() as tx:
        assert await tx.update_work_item(doubt, WorkItemState.DISPATCHING)
        listed = await tx.work_items_in_doubt()
    assert [(i.id, i.state, i.channel) for i in listed] == [
        (item.id, WorkItemState.RECONCILIATION_REQUIRED, WorkChannel.CONTACT_CENTER)
    ]

    now = SLOT_0900 + timedelta(minutes=10)
    queued, fenced = attempts.fence(request, doubt, now=now)
    async with world.uow.locked() as tx:
        assert await tx.update_work_item(fenced, WorkItemState.RECONCILIATION_REQUIRED)
        assert await tx.update_service_request(queued, request.handling)
        assert await tx.work_items_in_doubt() == []
        assert await tx.get_active_work_item(request.id or 0) is None
        [kept] = await tx.work_items_of(request.id or 0)
    assert (kept.state, kept.channel, kept.ended_at) == (
        WorkItemState.FENCED,
        WorkChannel.CONTACT_CENTER,
        now,
    )
