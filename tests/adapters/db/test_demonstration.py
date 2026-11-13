"""The demonstration fixture of ADR 0121 (step 10, gate item G08), on SQL and on the fake.

The in-person flow draws on a pool of two units, the document review on its own pool of one
(the video flow of the brief follows once video is built for a new flow). With the gate opened
in the test, the shared pool takes two appointments at one time and refuses a third, and the
own pool is untouched by them (A11, A12).
"""

from dataclasses import replace

import pytest

from app.adapters.db.demonstration import (
    IN_PERSON_FLOW,
    REVIEW_FLOW,
    Demonstration,
    seed_demonstration,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.service_flows import FlowState
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, another_participant
from tests.support.flow_world import EVERYTHING, World
from tests.support.pool_flows import open_gate


async def _seed(world: World) -> Demonstration:
    async with world.uow.locked() as tx:
        group = await tx.get_group_by_id(world.acme_group)
        assert group is not None
        return await seed_demonstration(tx, group, world.acme_staff)


async def test_each_flow_draws_on_its_own_pool_and_a_rerun_changes_nothing(
    world: World,
) -> None:
    first = await _seed(world)
    again = await _seed(world)

    async with world.uow.read() as tx:
        flows = {f.code: f for f in await tx.list_service_flows(EVERYTHING, world.acme_group)}
    assert again == first
    assert first.shared_pool != first.own_pool
    assert flows[IN_PERSON_FLOW].state is FlowState.ACTIVE
    assert flows[REVIEW_FLOW].state is FlowState.ACTIVE
    in_person = flows[IN_PERSON_FLOW].current
    assert in_person is not None
    assert in_person.capacity_pool_id == first.shared_pool
    review = flows[REVIEW_FLOW].current
    assert review is not None
    assert review.capacity_pool_id == first.own_pool


async def test_with_the_gate_open_the_shared_pool_takes_two_and_the_own_pool_is_untouched(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(world)
    open_gate(monkeypatch)
    service = BookingService(world.uow, FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0)
    async with world.uow.read() as tx:
        group = await tx.get_group_by_id(world.acme_group)
    assert group is not None
    subject = group.routing.subjects[0].code  # the service keeps the subjects it had

    async def book(flow: str) -> None:
        await service.book(
            group_slug=group.slug,
            start=SLOT_0900,
            contact=replace(another_participant(), answers={"_subject": subject}),
            locale="nl",
            flow=flow,
        )

    before = await service.availability(
        group.slug, SLOT_0900.date(), SLOT_0900.date(), flow=REVIEW_FLOW
    )
    await book(IN_PERSON_FLOW)
    await book(IN_PERSON_FLOW)
    with pytest.raises(DomainError) as full:
        await book(IN_PERSON_FLOW)
    review = await service.availability(
        group.slug, SLOT_0900.date(), SLOT_0900.date(), flow=REVIEW_FLOW
    )

    assert full.value.code is ErrorCode.SLOT_FULL
    assert review == before  # the own pool is untouched by the shared pool's bookings
    assert review
