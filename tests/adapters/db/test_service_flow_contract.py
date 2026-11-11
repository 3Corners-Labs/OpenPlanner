"""Skills, grants, pools and draft flows: one set of cases for the SQL adapter and the fake.

ADR 0121 step 2b. Each test runs against ``SqlUnitOfWork`` on a migrated database and against
``InMemoryUnitOfWork``, so the fake the domain tests use cannot drift from the adapter. The two
worlds are seeded through the same ``Transaction`` methods (two organisations, a group and a
staff member in each); only *activating* a flow, a later step, is done behind the port.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import FlowRevisionRow, ServiceFlowRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Organisation
from app.domain.service_flows import FlowState, IntakeMode, ServiceFlow
from app.domain.skills import Skill, SkillUsage
from app.domain.staff import Membership, StaffMember, StaffStatus
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW, make_group
from tests.support.service_flows import flow as make_flow
from tests.support.service_flows import legacy, revision

EVERYTHING = Scope(everything=True)
ACME = Scope(organisations=frozenset({"acme"}))
BETA = Scope(organisations=frozenset({"beta"}))
ACME_GROUP = Scope(groups=frozenset({("acme", "balie")}))
NOBODY = Scope()
LATER = datetime(2026, 2, 1, 9, 0, tzinfo=UTC)


@dataclass
class World:
    """Two organisations with a group and a staff member each, on either backend."""

    uow: Any
    acme: int
    beta: int
    acme_group: int
    beta_group: int
    acme_staff: int
    beta_staff: int
    activate: Callable[..., Awaitable[None]]


async def _seed(uow: Any) -> tuple[int, int, int, int, int, int]:
    """Seed through the port; return the ids of the organisations, groups and staff."""
    async with uow.locked() as tx:
        acme = await tx.add_organisation(Organisation(0, "Acme", "acme"))
        beta = await tx.add_organisation(Organisation(0, "Beta", "beta"))
        a_group = await tx.add_group(make_group(id=0, organisation_id=acme.id, slug="balie"))
        b_group = await tx.add_group(make_group(id=0, organisation_id=beta.id, slug="loket"))
        for subject, slug in (("sub-a", "acme"), ("sub-b", "beta")):
            await tx.save_staff(
                StaffMember(
                    subject,
                    "Staff",
                    frozenset(),
                    frozenset({Membership("org_admin", slug)}),
                    NOW,
                    NOW,
                    NOW,
                )
            )
        a_staff = (await tx.get_staff("sub-a")).id  # type: ignore[union-attr]
        b_staff = (await tx.get_staff("sub-b")).id  # type: ignore[union-attr]
    return acme.id, beta.id, a_group.id, b_group.id, a_staff, b_staff  # type: ignore[return-value]


@pytest.fixture(params=["sql", "fake"])
async def world(request: pytest.FixtureRequest, database_url: str) -> AsyncIterator[World]:
    if request.param == "fake":
        uow: Any = InMemoryUnitOfWork()

        async def activate(flow_id: int, state: FlowState = FlowState.ACTIVE) -> None:
            flow = uow.state.flows[flow_id]
            published = replace(flow.current, published_at=NOW)
            uow.state.flows[flow_id] = replace(flow, state=state, current=published)
            uow.state.flow_revisions[(flow_id, published.revision)] = published

        ids = await _seed(uow)
        yield World(uow, *ids, activate)
        return
    engine: AsyncEngine = create_engine(database_url, busy_timeout_ms=200)
    uow = SqlUnitOfWork(engine)

    async def activate_sql(flow_id: int, state: FlowState = FlowState.ACTIVE) -> None:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                update(ServiceFlowRow).where(ServiceFlowRow.id == flow_id).values(state=state.value)
            )
            await session.execute(
                update(FlowRevisionRow)
                .where(FlowRevisionRow.flow_id == flow_id)
                .values(published_at=NOW)
            )

    ids = await _seed(uow)
    yield World(uow, *ids, activate_sql)
    await engine.dispose()


def refused(code: ErrorCode, field: str | None = None) -> Any:
    """Return a ``pytest.raises`` for a domain error with this code and field."""
    return _Refusal(code, field)


class _Refusal:
    """Context manager asserting a ``DomainError`` of a given code and field."""

    def __init__(self, code: ErrorCode, field: str | None) -> None:
        self.code, self.field = code, field

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: Any, error: Any, trace: Any) -> bool:
        assert isinstance(error, DomainError), f"no DomainError, got {error!r}"
        assert (error.code, error.field) == (self.code, self.field)
        return True


async def add_skill(world: World, code: str = "passport", organisation: str = "acme") -> Skill:
    """Store a skill of ``organisation`` and return it."""
    organisation_id = world.acme if organisation == "acme" else world.beta
    async with world.uow.locked() as tx:
        return await tx.save_skill(
            Skill(None, organisation_id, code, {"nl": code.title(), "en": code.title()})
        )


async def add_pool(world: World, name: str = "Loket", skills: frozenset[int] = frozenset()) -> Any:
    """Store a counted pool of Acme and return it."""
    async with world.uow.locked() as tx:
        return await tx.save_capacity_pool(
            CapacityPool(None, world.acme, name, PoolMode.COUNTED, skills)
        )


def blank(
    world: World,
    code: str = "a",
    state: FlowState = FlowState.DRAFT,
    group: int | None = None,
    **changes: Any,
) -> ServiceFlow:
    """Return an unsaved flow of Acme's group (or ``group``) without a pool."""
    current = revision(**{"capacity_pool_id": None, **changes})
    flow = make_flow(code, state, None, frozenset(), current)
    return replace(flow, group_id=group if group is not None else world.acme_group)


async def add_flow(world: World, code: str = "a", **changes: Any) -> ServiceFlow:
    """Store a draft flow of Acme's group and return it."""
    async with world.uow.locked() as tx:
        return await tx.save_draft(blank(world, code, **changes))


# --- skills -----------------------------------------------------------------------------------


async def test_a_skill_is_stored_listed_and_updated(world: World) -> None:
    stored = await add_skill(world)
    assert stored.id is not None

    async with world.uow.locked() as tx:
        await tx.save_skill(replace(stored, labels={"nl": "Pas", "en": "Pass", "de": "Pass"}))
    async with world.uow.read() as tx:
        listed = await tx.list_skills(ACME, world.acme)
        found = await tx.get_skill(ACME, stored.id)

    assert [s.code for s in listed] == ["passport"]
    assert found is not None
    assert found.labels == {"nl": "Pas", "en": "Pass", "de": "Pass"}


async def test_a_skill_code_is_unique_per_organisation_only(world: World) -> None:
    await add_skill(world)
    await add_skill(world, organisation="beta")  # the same code in another organisation

    with refused(ErrorCode.SLUG_TAKEN, "skill_code"):
        await add_skill(world)


async def test_a_skill_of_another_organisation_is_invisible_and_cannot_be_overwritten(
    world: World,
) -> None:
    skill = await add_skill(world)
    assert skill.id is not None

    async with world.uow.read() as tx:
        assert await tx.get_skill(BETA, skill.id) is None
        assert await tx.get_skill(NOBODY, skill.id) is None
        assert await tx.list_skills(BETA, world.acme) == []
        assert await tx.list_skills(EVERYTHING, world.acme) == [skill]
    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "organisation_id"):
        async with world.uow.locked() as tx:
            await tx.save_skill(replace(skill, organisation_id=world.beta))


async def test_a_group_scope_reads_the_organisations_catalogue(world: World) -> None:
    skill = await add_skill(world)
    pool = await add_pool(world)

    async with world.uow.read() as tx:
        assert await tx.list_skills(ACME_GROUP, world.acme) == [skill]
        assert await tx.list_capacity_pools(ACME_GROUP, world.acme) == [pool]


async def test_an_archived_skill_leaves_the_list_but_keeps_what_refers_to_it(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    pool = await add_pool(world, skills=frozenset({skill.id}))
    flow = await add_flow(world, required_skill_ids=frozenset({skill.id}))
    async with world.uow.locked() as tx:
        await tx.set_staff_skills(world.acme_staff, {skill.id}, granted_by=None, at=NOW)

    async with world.uow.locked() as tx:
        await tx.archive_skill(skill.id, LATER)
        await tx.archive_skill(skill.id, NOW)  # idempotent: the first time stays

    async with world.uow.read() as tx:
        assert await tx.list_skills(ACME, world.acme) == []
        (archived,) = await tx.list_skills(ACME, world.acme, archived=True)
        assert archived.archived_at == LATER
        assert await tx.list_staff_skills(ACME, world.acme_staff) == {skill.id}
        (kept_pool,) = await tx.list_capacity_pools(ACME, world.acme)
        assert kept_pool.guaranteed_skill_ids == {skill.id}
        assert kept_pool.id == pool.id
        assert flow.id is not None
        stored = await tx.get_service_flow(ACME, flow.id)
        assert stored is not None
        assert stored.current.required_skill_ids == {skill.id}
        assert await tx.skill_usage(skill.id) == SkillUsage(staff=1, pools=1, flows=1)


async def test_a_skill_is_counted_for_a_subject_requirement_once_per_flow(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    await add_flow(
        world,
        required_skill_ids=frozenset({skill.id}),
        subject_skill_ids={"id": frozenset({skill.id}), "visa": frozenset()},
    )

    async with world.uow.read() as tx:
        assert await tx.skill_usage(skill.id) == SkillUsage(staff=0, pools=0, flows=1)


async def test_an_unknown_skill_cannot_be_archived_or_saved(world: World) -> None:
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id"):
        async with world.uow.locked() as tx:
            await tx.archive_skill(999, NOW)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "organisation_id"):
        async with world.uow.locked() as tx:
            await tx.save_skill(Skill(None, 999, "x", {"nl": "X", "en": "X"}))


# --- staff grants -----------------------------------------------------------------------------


async def test_staff_skills_are_replaced_as_a_set_and_keep_unchanged_grants(world: World) -> None:
    one, two, three = [await add_skill(world, c) for c in ("one", "two", "three")]
    assert one.id
    assert two.id
    assert three.id
    async with world.uow.locked() as tx:
        await tx.set_staff_skills(world.acme_staff, {one.id, two.id}, granted_by=None, at=NOW)
    async with world.uow.locked() as tx:
        await tx.set_staff_skills(
            world.acme_staff, [two.id, three.id], granted_by=world.acme_staff, at=LATER
        )

    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(ACME, world.acme_staff) == {two.id, three.id}
        assert await tx.staff_with_skill(ACME, two.id) == [world.acme_staff]
        assert await tx.staff_with_skill(ACME, one.id) == []
        assert await tx.staff_with_skill(BETA, two.id) == []  # another organisation's scope
        assert await tx.list_staff_skills(BETA, world.acme_staff) == frozenset()
        assert await tx.list_staff_skills(NOBODY, world.acme_staff) == frozenset()


async def test_a_staff_member_holds_no_skill_of_an_organisation_they_have_no_place_in(
    world: World,
) -> None:
    other = await add_skill(world, organisation="beta")
    assert other.id is not None

    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "skill_ids"):
        async with world.uow.locked() as tx:
            await tx.set_staff_skills(world.acme_staff, {other.id}, granted_by=None, at=NOW)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_ids"):
        async with world.uow.locked() as tx:
            await tx.set_staff_skills(world.acme_staff, {999}, granted_by=None, at=NOW)


async def test_an_archived_skill_is_not_granted_but_an_old_grant_stays_or_goes(
    world: World,
) -> None:
    held, new = await add_skill(world, "held"), await add_skill(world, "new")
    assert held.id
    assert new.id
    async with world.uow.locked() as tx:
        await tx.set_staff_skills(world.acme_staff, {held.id}, granted_by=None, at=NOW)
        await tx.archive_skill(held.id, NOW)
        await tx.archive_skill(new.id, NOW)

    with refused(ErrorCode.INVALID_CONFIGURATION, "skill_ids"):
        async with world.uow.locked() as tx:
            await tx.set_staff_skills(world.acme_staff, {held.id, new.id}, granted_by=None, at=NOW)
    async with world.uow.locked() as tx:  # keeping the archived grant is fine
        await tx.set_staff_skills(world.acme_staff, {held.id}, granted_by=None, at=LATER)
    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(ACME, world.acme_staff) == {held.id}
    async with world.uow.locked() as tx:  # and removing it is always allowed
        await tx.set_staff_skills(world.acme_staff, set(), granted_by=None, at=LATER)
    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(ACME, world.acme_staff) == frozenset()


async def test_an_erased_or_unknown_staff_member_has_no_skills(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    async with world.uow.locked() as tx:
        await tx.set_staff_skills(world.acme_staff, {skill.id}, granted_by=None, at=NOW)
        await tx.set_staff_status("sub-a", StaffStatus.SUSPENDED, NOW)
        await tx.erase_staff("sub-a", LATER)

    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(EVERYTHING, world.acme_staff) == frozenset()
        assert await tx.staff_with_skill(EVERYTHING, skill.id) == []
    for staff_id in (world.acme_staff, 999):
        with refused(ErrorCode.STAFF_NOT_FOUND):
            async with world.uow.locked() as tx:
                await tx.set_staff_skills(staff_id, {skill.id}, granted_by=None, at=LATER)


# --- capacity pools ---------------------------------------------------------------------------


async def test_a_pool_keeps_its_guaranteed_skills_and_replaces_them_on_update(world: World) -> None:
    one, two = await add_skill(world, "one"), await add_skill(world, "two")
    assert one.id
    assert two.id
    pool = await add_pool(world, skills=frozenset({one.id}))
    assert pool.id is not None
    assert pool.guaranteed_skill_ids == {one.id}

    async with world.uow.locked() as tx:
        await tx.save_capacity_pool(replace(pool, name="Kantoor", guaranteed_skill_ids={two.id}))
    async with world.uow.read() as tx:
        found = await tx.get_capacity_pool(ACME, pool.id)
        assert await tx.get_capacity_pool(BETA, pool.id) is None
        assert await tx.list_capacity_pools(BETA, world.acme) == []

    assert found is not None
    assert (found.name, found.guaranteed_skill_ids) == ("Kantoor", frozenset({two.id}))


async def test_a_pool_guarantees_only_own_unarchived_skills(world: World) -> None:
    other = await add_skill(world, organisation="beta")
    old = await add_skill(world, "old")
    assert other.id
    assert old.id
    async with world.uow.locked() as tx:
        await tx.archive_skill(old.id, NOW)

    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "guaranteed_skill_ids"):
        await add_pool(world, skills=frozenset({other.id}))
    with refused(ErrorCode.INVALID_CONFIGURATION, "guaranteed_skill_ids"):
        await add_pool(world, skills=frozenset({old.id}))
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "guaranteed_skill_ids"):
        await add_pool(world, skills=frozenset({999}))


async def test_a_legacy_group_pool_wraps_one_group_of_its_organisation(world: World) -> None:
    async def legacy_pool(organisation: int, group: int | None) -> CapacityPool:
        async with world.uow.locked() as tx:
            return await tx.save_capacity_pool(
                CapacityPool(None, organisation, "Balie", PoolMode.LEGACY_GROUP, group_id=group)
            )

    pool = await legacy_pool(world.acme, world.acme_group)
    assert pool.group_id == world.acme_group

    with refused(ErrorCode.CONFIGURATION_OBJECT_EXISTS, "group_id"):
        await legacy_pool(world.acme, world.acme_group)
    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "group_id"):
        await legacy_pool(world.acme, world.beta_group)
    with refused(ErrorCode.INVALID_CONFIGURATION, "group_id"):
        await legacy_pool(world.acme, None)
    with refused(ErrorCode.INVALID_CONFIGURATION, "group_id"):
        await add_pool_with_group(world)
    with refused(ErrorCode.INVALID_CONFIGURATION, "mode"):  # mode and group never change
        async with world.uow.locked() as tx:
            assert pool.id is not None
            await tx.save_capacity_pool(replace(pool, mode=PoolMode.COUNTED, group_id=None))


async def add_pool_with_group(world: World) -> None:
    """Try to store a counted pool that names a group."""
    async with world.uow.locked() as tx:
        await tx.save_capacity_pool(
            CapacityPool(None, world.acme, "X", PoolMode.COUNTED, group_id=world.acme_group)
        )


async def test_a_pool_of_another_organisation_cannot_be_overwritten(world: World) -> None:
    pool = await add_pool(world)

    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "organisation_id"):
        async with world.uow.locked() as tx:
            await tx.save_capacity_pool(replace(pool, organisation_id=world.beta))


async def test_a_pool_with_an_active_flow_cannot_be_archived(world: World) -> None:
    pool = await add_pool(world)
    assert pool.id is not None
    draft = await add_flow(world, capacity_pool_id=pool.id)
    assert draft.id is not None
    assert [flow_id async for flow_id in _flows_on(world, pool.id)] == [draft.id]

    async with world.uow.locked() as tx:  # a draft does not hold the pool
        await tx.archive_capacity_pool(pool.id, NOW)
    async with world.uow.read() as tx:
        assert await tx.list_capacity_pools(ACME, world.acme) == []
        (archived,) = await tx.list_capacity_pools(ACME, world.acme, archived=True)
    assert archived.archived_at == NOW

    busy = await add_pool(world, "Busy")
    assert busy.id is not None
    flow = await add_flow(world, "b", capacity_pool_id=busy.id)
    assert flow.id is not None
    await world.activate(flow.id)
    with refused(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "capacity_pool"):
        async with world.uow.locked() as tx:
            await tx.archive_capacity_pool(busy.id, NOW)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id"):
        async with world.uow.locked() as tx:
            await tx.archive_capacity_pool(999, NOW)


async def _flows_on(world: World, pool_id: int) -> AsyncIterator[int]:
    async with world.uow.read() as tx:
        for flow_id in await tx.flows_on_pool(pool_id):
            yield flow_id


# --- flows ------------------------------------------------------------------------------------


async def test_a_draft_flow_round_trips_with_its_skills_and_subjects(world: World) -> None:
    skill = await add_skill(world)
    pool = await add_pool(world)
    assert skill.id is not None
    assert pool.id is not None
    stored = await add_flow(
        world,
        capacity_pool_id=pool.id,
        required_skill_ids=frozenset({skill.id}),
        subject_skill_ids={"id": frozenset({skill.id}), "visa": frozenset()},
    )

    assert stored.id is not None
    assert stored.current.flow_id == stored.id
    async with world.uow.read() as tx:
        listed = await tx.list_service_flows(ACME, world.acme_group)
        found = await tx.get_service_flow(EVERYTHING, stored.id)
        revision_one = await tx.flow_revision(stored.id, 1)
        missing = await tx.flow_revision(stored.id, 2)

    assert listed == [stored]
    assert found == stored
    assert revision_one == stored.current
    assert missing is None
    assert stored.state is FlowState.DRAFT
    assert stored.current.published_at is None
    assert stored.current.subject_skill_ids == {
        "id": frozenset({skill.id}),
        "visa": frozenset(),
    }


async def test_a_flow_of_another_organisation_is_invisible(world: World) -> None:
    flow = await add_flow(world)
    assert flow.id is not None

    async with world.uow.read() as tx:
        assert await tx.get_service_flow(BETA, flow.id) is None
        assert await tx.get_service_flow(NOBODY, flow.id) is None
        assert await tx.list_service_flows(BETA, world.acme_group) == []
        assert await tx.list_service_flows(ACME_GROUP, world.acme_group) == [flow]
        assert await tx.list_service_flows(ACME, world.beta_group) == []


async def test_saving_a_draft_again_overwrites_its_unpublished_revision(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    stored = await add_flow(world, required_skill_ids=frozenset({skill.id}))

    changed = replace(
        stored,
        code="b",
        default_for=frozenset({IntakeMode.APPOINTMENT}),
        current=replace(stored.current, duration_minutes=45, required_skill_ids=frozenset()),
    )
    async with world.uow.locked() as tx:
        again = await tx.save_draft(changed)
    async with world.uow.read() as tx:
        listed = await tx.list_service_flows(ACME, world.acme_group)

    assert listed == [again]
    assert (again.code, again.current.duration_minutes) == ("b", 45)
    assert again.default_for == {IntakeMode.APPOINTMENT}
    assert again.current.required_skill_ids == frozenset()


async def test_a_flow_code_is_unique_per_group(world: World) -> None:
    await add_flow(world, "a")
    other = await add_flow(world, "b")

    with refused(ErrorCode.SLUG_TAKEN, "flow_code"):
        await add_flow(world, "a")
    with refused(ErrorCode.SLUG_TAKEN, "flow_code"):
        async with world.uow.locked() as tx:
            await tx.save_draft(replace(other, code="a"))
    async with world.uow.locked() as tx:  # the same code in another group is fine
        await tx.save_draft(blank(world, "a", group=world.beta_group))


async def test_a_published_revision_is_never_changed(world: World) -> None:
    stored = await add_flow(world)
    assert stored.id is not None
    await world.activate(stored.id)

    with refused(ErrorCode.INVALID_CONFIGURATION, "state"):
        async with world.uow.locked() as tx:
            await tx.save_draft(
                replace(stored, current=replace(stored.current, duration_minutes=5))
            )
    async with world.uow.read() as tx:
        unchanged = await tx.get_service_flow(EVERYTHING, stored.id)
    assert unchanged is not None
    assert unchanged.current.duration_minutes == 20
    assert unchanged.current.published_at == NOW


async def test_a_draft_whose_revision_is_published_is_refused(world: World) -> None:
    """The revision, not only the flow's state, protects a published revision."""
    stored = await add_flow(world)
    assert stored.id is not None
    await world.activate(stored.id, FlowState.DRAFT)

    with refused(ErrorCode.INVALID_CONFIGURATION, "revision_published"):
        async with world.uow.locked() as tx:
            await tx.save_draft(replace(stored, code="z"))
    with refused(ErrorCode.INVALID_CONFIGURATION, "published_at"):
        async with world.uow.locked() as tx:
            await tx.save_draft(blank(world, "n", published_at=NOW))


async def test_a_draft_names_a_new_flow_and_the_stored_revision_only(world: World) -> None:
    stored = await add_flow(world)

    async def save(flow: ServiceFlow) -> None:
        async with world.uow.locked() as tx:
            await tx.save_draft(flow)

    with refused(ErrorCode.INVALID_CONFIGURATION, "revision"):
        await save(blank(world, "n", revision=2))
    with refused(ErrorCode.INVALID_CONFIGURATION, "revision"):
        await save(replace(stored, current=replace(stored.current, revision=2)))
    with refused(ErrorCode.INVALID_CONFIGURATION, "state"):
        await save(blank(world, "n", FlowState.ACTIVE))
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id"):
        await save(replace(stored, id=999, current=replace(stored.current, flow_id=999)))
    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "group_id"):
        await save(replace(stored, group_id=world.beta_group))
    with refused(ErrorCode.GROUP_NOT_FOUND):
        await save(blank(world, "n", group=999))


async def test_a_flow_draws_only_on_its_organisations_pool_and_skills(world: World) -> None:
    other_skill = await add_skill(world, organisation="beta")
    async with world.uow.locked() as tx:
        other_pool = await tx.save_capacity_pool(
            CapacityPool(None, world.beta, "Elders", PoolMode.COUNTED)
        )
    assert other_skill.id is not None
    assert other_pool.id is not None

    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "capacity_pool_id"):
        await add_flow(world, capacity_pool_id=other_pool.id)
    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "required_skill_ids"):
        await add_flow(world, required_skill_ids=frozenset({other_skill.id}))
    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "required_skill_ids"):
        await add_flow(world, subject_skill_ids={"id": frozenset({other_skill.id})})
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "capacity_pool_id"):
        await add_flow(world, capacity_pool_id=999)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "required_skill_ids"):
        await add_flow(world, required_skill_ids=frozenset({999}))


async def test_an_archived_skill_is_not_newly_required_but_a_held_one_stays(world: World) -> None:
    held, fresh = await add_skill(world, "held"), await add_skill(world, "fresh")
    assert held.id
    assert fresh.id
    stored = await add_flow(world, required_skill_ids=frozenset({held.id}))
    async with world.uow.locked() as tx:
        await tx.archive_skill(held.id, NOW)
        await tx.archive_skill(fresh.id, NOW)

    async def save(skills: frozenset[int]) -> ServiceFlow:
        async with world.uow.locked() as tx:
            return await tx.save_draft(
                replace(stored, current=replace(stored.current, required_skill_ids=skills))
            )

    with refused(ErrorCode.INVALID_CONFIGURATION, "required_skill_ids"):
        await save(frozenset({held.id, fresh.id}))
    assert (await save(frozenset({held.id}))).current.required_skill_ids == {held.id}


async def test_the_legacy_flag_and_location_survive_a_draft(world: World) -> None:
    stored = await add_flow(world, location_id=7, collects_email=False)

    assert (stored.current.location_id, stored.current.collects_email) == (7, False)
    assert stored.current.legacy is False
    assert legacy().legacy  # the builder of legacy revisions is what the migration mirrors
