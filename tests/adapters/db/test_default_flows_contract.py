"""The legacy pool and flows of every group: one set of cases for the SQL adapter and the fake.

ADR 0121 step 2e. Each test runs against ``SqlUnitOfWork`` on a migrated database and against
``InMemoryUnitOfWork``. The rule itself is ``app.domain.default_flows`` (tested there); these
tests pin that the writers of a group and of its weekly rules call it in their own transaction,
that ``ensure_default_flows`` is idempotent and additive, and that the repair walk counts groups.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import time
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import CapacityPoolRow, ServiceFlowRow
from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.default_flows_service import RepairCounts, ensure_all_default_flows
from app.domain.errors import DomainError
from app.domain.scheduling import DispatchKind, Offer, Organisation, ScheduleRule, TimeWindow
from app.domain.service_flows import (
    CompletionPolicy,
    FlowState,
    IntakeMode,
    ServiceFlow,
)
from app.domain.service_flows import (
    DispatchKind as FlowDispatch,
)
from tests.support.domain import NOW, make_group
from tests.support.flow_backends import BACKENDS, CountingUow, open_backend

EVERYTHING = Scope(everything=True)


@dataclass
class World:
    """One organisation on either backend, and a way to forget the legacy rows of a group."""

    uow: Any
    organisation: int
    forget: Callable[[], Awaitable[None]]
    """Delete every pool and flow, as a database an older image wrote to looks."""


@pytest.fixture(params=BACKENDS)
async def world(request: pytest.FixtureRequest, database_url: str) -> AsyncIterator[World]:
    async with open_backend(request.param, database_url) as backend:
        async with backend.uow.locked() as tx:
            org = await tx.add_organisation(Organisation(0, "Acme", "acme"))
        yield World(backend.uow, org.id, backend.forget)


async def add_group(world: World, slug: str = "balie", **changes: Any) -> int:
    """Store a group through the port and return its id."""
    async with world.uow.locked() as tx:
        group = await tx.add_group(
            make_group(id=0, organisation_id=world.organisation, slug=slug, **changes)
        )
    return int(group.id)


def block(offer: Offer, capacity: int = 2) -> ScheduleRule:
    """Return a Monday rule with the given offer and capacity."""
    return ScheduleRule(0, TimeWindow(time(9), time(10), capacity, None, offer))


async def flows_of(world: World, group_id: int) -> dict[str, ServiceFlow]:
    """Return the group's flows by code."""
    async with world.uow.read() as tx:
        return {f.code: f for f in await tx.list_service_flows(EVERYTHING, group_id)}


async def pools_of(world: World) -> list[CapacityPool]:
    """Return the organisation's pools."""
    async with world.uow.read() as tx:
        return list(await tx.list_capacity_pools(EVERYTHING, world.organisation, archived=False))


async def test_a_new_group_gets_its_legacy_pool_and_appointment_flow(world: World) -> None:
    group_id = await add_group(world, name="Balie", slot_minutes=20)

    (pool,) = await pools_of(world)
    flows = await flows_of(world, group_id)

    assert (pool.name, pool.mode, pool.group_id) == ("Balie", PoolMode.LEGACY_GROUP, group_id)
    assert list(flows) == ["appointment"]
    flow = flows["appointment"]
    assert (flow.state, flow.default_for, flow.draft) == (
        FlowState.ACTIVE,
        frozenset({IntakeMode.APPOINTMENT}),
        None,
    )
    assert flow.current is not None
    assert flow.current.capacity_pool_id == pool.id
    assert flow.current.legacy
    assert flow.current.duration_minutes == 20
    assert flow.current.dispatch is FlowDispatch.CONTACT_CENTER
    assert flow.current.completion is CompletionPolicy.LEGACY_VIDEO_OBSERVATION
    assert flow.current.published_at is not None
    assert flow.current.published_by is None


async def test_a_group_on_the_own_queue_gets_the_own_queue_dispatch(world: World) -> None:
    group_id = await add_group(world, dispatch_kind=DispatchKind.OPENPLANNER_QUEUE)

    current = (await flows_of(world, group_id))["appointment"].current

    assert current is not None
    assert current.dispatch is FlowDispatch.OWN_QUEUE


async def test_an_archived_group_gets_archived_flows(world: World) -> None:
    group_id = await add_group(world, archived_at=NOW)

    flows = await flows_of(world, group_id)

    assert {f.state for f in flows.values()} == {FlowState.ARCHIVED}
    assert len(await pools_of(world)) == 1


async def test_a_rule_that_offers_the_direct_call_adds_the_direct_flow(world: World) -> None:
    group_id = await add_group(world)
    async with world.uow.locked() as tx:
        await tx.add_schedule_rule(group_id, block(Offer.SCHEDULED))
    assert list(await flows_of(world, group_id)) == ["appointment"]

    async with world.uow.locked() as tx:
        await tx.add_schedule_rule(group_id, block(Offer.BOTH))
    flows = await flows_of(world, group_id)

    assert list(flows) == ["appointment", "direct"]
    assert flows["direct"].default_for == frozenset({IntakeMode.ON_DEMAND})
    assert flows["direct"].current is not None
    assert flows["direct"].current.intake_modes == frozenset({IntakeMode.ON_DEMAND})
    assert len(await pools_of(world)) == 1  # both flows draw on the one pool


async def test_changing_a_rule_to_offer_the_direct_call_adds_the_direct_flow(
    world: World,
) -> None:
    group_id = await add_group(world)
    async with world.uow.locked() as tx:
        rule_id = await tx.add_schedule_rule(group_id, block(Offer.SCHEDULED))
    async with world.uow.locked() as tx:
        assert await tx.update_schedule_rule(group_id, rule_id, block(Offer.DIRECT))

    assert "direct" in await flows_of(world, group_id)


async def test_a_rule_without_agents_or_a_rule_that_is_gone_adds_nothing(world: World) -> None:
    group_id = await add_group(world)
    async with world.uow.locked() as tx:
        rule_id = await tx.add_schedule_rule(group_id, block(Offer.DIRECT, capacity=0))
        assert not await tx.update_schedule_rule(group_id, rule_id + 99, block(Offer.DIRECT))

    assert list(await flows_of(world, group_id)) == ["appointment"]


async def test_the_direct_flow_stays_when_the_rule_goes(world: World) -> None:
    group_id = await add_group(world)
    async with world.uow.locked() as tx:
        rule_id = await tx.add_schedule_rule(group_id, block(Offer.DIRECT))
    async with world.uow.locked() as tx:
        assert await tx.delete_schedule_rule(group_id, rule_id)
        assert not await tx.ensure_default_flows(group_id)

    assert list(await flows_of(world, group_id)) == ["appointment", "direct"]


async def test_ensuring_twice_creates_nothing_the_second_time(world: World) -> None:
    group_id = await add_group(world)
    async with world.uow.locked() as tx:
        await tx.add_schedule_rule(group_id, block(Offer.BOTH))
    before = await flows_of(world, group_id)

    async with world.uow.locked() as tx:
        created = await tx.ensure_default_flows(group_id)

    assert created is False
    assert await flows_of(world, group_id) == before
    assert len(await pools_of(world)) == 1


async def test_existing_pools_and_flows_are_never_touched(world: World) -> None:
    group_id = await add_group(world)
    async with world.uow.locked() as tx:
        flow = (await tx.list_service_flows(EVERYTHING, group_id))[0]
        assert flow.id is not None
        await tx.set_flow_state(EVERYTHING, flow.id, FlowState.ARCHIVED)
        await tx.add_schedule_rule(group_id, block(Offer.DIRECT))
    flows = await flows_of(world, group_id)
    assert flows["appointment"].state is FlowState.ARCHIVED  # not made active again
    (pool,) = await pools_of(world)
    async with world.uow.locked() as tx:
        await tx.save_capacity_pool(EVERYTHING, replace(pool, name="Eigen naam"))
        await tx.save_group(replace(await tx.get_group_by_id(group_id), name="Hernoemd"))
        assert not await tx.ensure_default_flows(group_id)

    assert (await pools_of(world))[0].name == "Eigen naam"
    assert await flows_of(world, group_id) == flows


async def test_a_group_that_does_not_exist_has_nothing_to_ensure(world: World) -> None:
    async with world.uow.locked() as tx:
        assert not await tx.ensure_default_flows(999)


async def test_a_legacy_revision_cannot_be_saved_as_a_draft(world: World) -> None:
    group_id = await add_group(world)
    flow = (await flows_of(world, group_id))["appointment"]
    assert flow.current is not None

    async with world.uow.locked() as tx:
        with pytest.raises(DomainError):
            await tx.save_draft(EVERYTHING, replace(flow, draft=flow.current, current=None))


async def test_a_legacy_flow_is_a_snapshot_of_the_service_when_it_was_made(world: World) -> None:
    """Later changes of the service never change its legacy rows (ADR 0121 point 8)."""
    group_id = await add_group(world, name="Balie", slot_minutes=20)
    before_pool = await pools_of(world)
    before = await flows_of(world, group_id)

    async with world.uow.locked() as tx:
        group = await tx.get_group_by_id(group_id)
        await tx.save_group(
            replace(
                group,
                name="Hernoemd",
                slot_minutes=45,
                dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
                archived_at=NOW,
            )
        )
        await tx.add_schedule_rule(group_id, block(Offer.SCHEDULED))
        assert not await tx.ensure_default_flows(group_id)

    assert await pools_of(world) == before_pool
    assert await flows_of(world, group_id) == before
    current = before["appointment"].current
    assert current is not None
    assert (current.labels["nl"], current.duration_minutes) == ("Balie", 20)
    assert (current.dispatch, before["appointment"].state) == (
        FlowDispatch.CONTACT_CENTER,
        FlowState.ACTIVE,
    )


async def test_the_repair_gives_a_group_of_an_older_image_its_rows_once(world: World) -> None:
    first = await add_group(world, slug="een")
    second = await add_group(world, slug="twee", archived_at=NOW)
    async with world.uow.locked() as tx:
        await tx.add_schedule_rule(first, block(Offer.BOTH))
    await world.forget()
    assert await pools_of(world) == []
    async with world.uow.read() as tx:
        assert await tx.groups_missing_default_flows() == [first, second]

    assert await ensure_all_default_flows(world.uow) == RepairCounts(2, 0)

    assert list(await flows_of(world, first)) == ["appointment", "direct"]
    assert {f.state for f in (await flows_of(world, second)).values()} == {FlowState.ARCHIVED}
    assert len(await pools_of(world)) == 2  # one legacy pool per group, the archived one included
    async with world.uow.read() as tx:
        assert await tx.groups_missing_default_flows() == []
    assert await ensure_all_default_flows(world.uow) == RepairCounts(0, 0)


async def test_a_group_with_a_pool_but_no_direct_flow_is_found_by_its_rules(world: World) -> None:
    group_id = await add_group(world)
    other = await add_group(world, slug="twee")
    async with world.uow.locked() as tx:
        await tx.add_schedule_rule(group_id, block(Offer.SCHEDULED))
        await tx.add_schedule_rule(other, block(Offer.DIRECT, capacity=0))
    async with world.uow.read() as tx:
        assert await tx.groups_missing_default_flows() == []
    async with world.uow.locked() as tx:
        await tx.add_schedule_rule(group_id, block(Offer.BOTH))
    await world.forget()
    async with world.uow.locked() as tx:
        await tx.ensure_default_flows(other)  # the other group is whole again, group_id is not
    async with world.uow.read() as tx:
        assert await tx.groups_missing_default_flows() == [group_id]


async def test_a_healthy_installation_takes_no_write_lock(world: World) -> None:
    await add_group(world)
    counting = CountingUow(world.uow)

    assert await ensure_all_default_flows(counting) == RepairCounts(0, 0)
    assert counting.writes == 0

    await world.forget()
    assert await ensure_all_default_flows(counting) == RepairCounts(1, 0)
    assert counting.writes == 1  # one short write transaction for the one group that needed it


async def test_every_demo_group_has_its_pool_and_appointment_flow(engine: AsyncEngine) -> None:
    from sqlalchemy import func, select

    from app.adapters.db.seed import seed_demo

    assert await seed_demo(engine)

    async with AsyncSession(engine) as session:
        flows = (await session.scalars(select(ServiceFlowRow.code))).all()
        pools = await session.scalar(select(func.count()).select_from(CapacityPoolRow))
    assert (list(flows), pools) == (["appointment"] * 2, 2)  # the seed makes two groups
