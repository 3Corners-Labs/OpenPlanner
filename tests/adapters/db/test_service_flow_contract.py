"""Skills, grants, pools and flows: one set of cases for the SQL adapter and the fake.

ADR 0121 step 2b. Each test runs against ``SqlUnitOfWork`` on a migrated database and against
``InMemoryUnitOfWork``, so the fake the domain tests use cannot drift from the adapter. The two
worlds are seeded through the same ``Transaction`` methods (two organisations, a group and a
staff member in each, and a person who belongs to both). The rules themselves are the pure
functions of ``app.domain`` and are tested there; these tests pin what the two backends *store*
and the scope every method filters by (an id outside the scope is a missing id).
"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolFlows, PoolMode
from app.domain.errors import ErrorCode
from app.domain.service_flows import FlowState, IntakeMode, ServiceFlow
from app.domain.skills import Skill, SkillUsage
from app.domain.staff import StaffStatus
from tests.support.domain import NOW, make_group
from tests.support.flow_world import EVERYTHING, LATER, World, add_pool, person, refused
from tests.support.service_flows import flow as make_flow
from tests.support.service_flows import revision

ACME = Scope(organisations=frozenset({"acme"}))
BETA = Scope(organisations=frozenset({"beta"}))
ACME_GROUP = Scope(groups=frozenset({("acme", "balie")}))
NOBODY = Scope()


def who_granted(world: World) -> Callable[[int, int], Awaitable[int | None]]:
    """Return the reader of who granted a skill, or skip: only the SQL rows keep it."""
    if world.granted_by is None:
        pytest.skip("who granted a skill is not readable through the port; the SQL variant checks")
    return world.granted_by


async def add_skill(world: World, code: str = "passport", organisation: str = "acme") -> Skill:
    """Store a skill of ``organisation`` and return it."""
    organisation_id = world.acme if organisation == "acme" else world.beta
    async with world.uow.locked() as tx:
        return await tx.save_skill(
            EVERYTHING, Skill(None, organisation_id, code, {"nl": code.title(), "en": code.title()})
        )


def blank(
    world: World,
    code: str = "a",
    group: int | None = None,
    **changes: Any,
) -> ServiceFlow:
    """Return an unsaved flow of Acme's group (or ``group``) whose draft has no pool."""
    draft = revision(**{"capacity_pool_id": None, **changes})
    flow = make_flow(code, FlowState.DRAFT, None, frozenset(), None, draft)
    return replace(flow, group_id=group if group is not None else world.acme_group)


async def add_flow(world: World, code: str = "a", **changes: Any) -> ServiceFlow:
    """Store a new flow of Acme's group (a draft with revision 1) and return it."""
    async with world.uow.locked() as tx:
        return await tx.save_draft(EVERYTHING, blank(world, code, **changes))


async def save_draft(world: World, flow: ServiceFlow, scope: Scope = EVERYTHING) -> ServiceFlow:
    """Save ``flow``'s draft."""
    async with world.uow.locked() as tx:
        return await tx.save_draft(scope, flow)


async def publish(world: World, flow_id: int, at: datetime = NOW) -> ServiceFlow:
    """Publish the draft of ``flow_id`` as ``Acme's admin``."""
    async with world.uow.locked() as tx:
        return await tx.publish_draft(EVERYTHING, flow_id, by=world.acme_staff, at=at)


async def activate(world: World, flow_id: int) -> ServiceFlow:
    """Publish the draft of a flow never published and make the flow active."""
    await publish(world, flow_id)
    async with world.uow.locked() as tx:
        return await tx.set_flow_state(EVERYTHING, flow_id, FlowState.ACTIVE)


async def get_flow(world: World, flow_id: int) -> ServiceFlow:
    """Return the flow, which must exist."""
    async with world.uow.read() as tx:
        found = await tx.get_service_flow(EVERYTHING, flow_id)
    assert found is not None
    return found


# --- skills -----------------------------------------------------------------------------------


async def test_a_skill_is_stored_listed_and_updated(world: World) -> None:
    stored = await add_skill(world)
    assert stored.id is not None

    async with world.uow.locked() as tx:
        await tx.save_skill(ACME, replace(stored, labels={"nl": "Pas", "en": "Pass", "de": "Pass"}))
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
            await tx.save_skill(EVERYTHING, replace(skill, organisation_id=world.beta))


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
        await tx.set_staff_skills(
            EVERYTHING, world.acme_staff, world.acme, {skill.id}, granted_by=None, at=NOW
        )

    async with world.uow.locked() as tx:
        await tx.archive_skill(ACME, skill.id, LATER)
        await tx.archive_skill(ACME, skill.id, NOW)  # idempotent: the first time stays

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
        assert stored.draft is not None
        assert stored.draft.required_skill_ids == {skill.id}
        assert await tx.skill_usage(ACME, skill.id) == SkillUsage(staff=1, pools=1, flows=1)


async def test_a_skill_is_counted_for_a_subject_requirement_once_per_flow(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    await add_flow(
        world,
        required_skill_ids=frozenset({skill.id}),
        subject_skill_ids={"id": frozenset({skill.id}), "visa": frozenset()},
    )

    async with world.uow.read() as tx:
        assert await tx.skill_usage(ACME, skill.id) == SkillUsage(staff=0, pools=0, flows=1)


async def test_skill_usage_counts_the_current_and_the_draft_of_flows_not_archived(
    world: World,
) -> None:
    old, new, other = [await add_skill(world, c) for c in ("old", "new", "other")]
    assert old.id
    assert new.id
    assert other.id
    flow = await add_flow(world, required_skill_ids=frozenset({old.id}))
    assert flow.id is not None
    await activate(world, flow.id)
    await save_draft(
        world,
        replace(
            flow,
            draft=revision(flow_id=flow.id, capacity_pool_id=None, required_skill_ids={new.id}),
        ),
    )

    async def usage(skill_id: int) -> SkillUsage:
        async with world.uow.read() as tx:
            return await tx.skill_usage(ACME, skill_id)

    assert (await usage(old.id)).flows == 1  # the published revision
    assert (await usage(new.id)).flows == 1  # the draft
    assert (await usage(other.id)).flows == 0
    async with world.uow.locked() as tx:
        await tx.set_flow_state(EVERYTHING, flow.id, FlowState.ARCHIVED)
    assert (await usage(old.id)).flows == 0
    assert (await usage(new.id)).flows == 0


async def test_skill_usage_counts_the_grants_of_current_members_only(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    await grant(world, world.acme_staff, {skill.id})
    await grant(world, world.both_staff, {skill.id})
    async with world.uow.read() as tx:
        assert (await tx.skill_usage(ACME, skill.id)).staff == 2
    async with world.uow.locked() as tx:  # the person leaves Acme; the grant row may stay
        await tx.save_staff(person("sub-ab", ["beta"]))

    async with world.uow.read() as tx:
        assert (await tx.skill_usage(ACME, skill.id)).staff == 1
        assert await tx.list_staff_skills(EVERYTHING, world.both_staff) == frozenset()
        assert await tx.list_staff_skills(EVERYTHING, world.acme_staff) == {skill.id}


async def test_a_skill_outside_the_scope_is_used_nowhere_and_cannot_be_archived_or_saved(
    world: World,
) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    async with world.uow.locked() as tx:
        await tx.set_staff_skills(
            EVERYTHING, world.acme_staff, world.acme, {skill.id}, granted_by=None, at=NOW
        )

    async with world.uow.read() as tx:
        assert await tx.skill_usage(BETA, skill.id) == SkillUsage()
        assert await tx.skill_usage(NOBODY, skill.id) == SkillUsage()
        assert await tx.skill_usage(BETA, 999) == SkillUsage()  # the same as a missing id
        assert await tx.skill_usage(EVERYTHING, skill.id) == SkillUsage(staff=1)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id"):
        async with world.uow.locked() as tx:
            await tx.archive_skill(BETA, skill.id, NOW)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id"):
        async with world.uow.locked() as tx:
            await tx.archive_skill(BETA, 999, NOW)  # the same as one outside the scope
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "organisation_id"):
        async with world.uow.locked() as tx:
            await tx.save_skill(BETA, replace(skill, labels={"nl": "X", "en": "X"}))
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id"):
        async with world.uow.locked() as tx:  # claims Beta, names Acme's id
            await tx.save_skill(BETA, replace(skill, organisation_id=world.beta))
    async with world.uow.read() as tx:
        assert (await tx.get_skill(EVERYTHING, skill.id)) == skill  # nothing changed


async def test_an_unknown_skill_cannot_be_archived_or_saved(world: World) -> None:
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id"):
        async with world.uow.locked() as tx:
            await tx.archive_skill(EVERYTHING, 999, NOW)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "organisation_id"):
        async with world.uow.locked() as tx:
            await tx.save_skill(EVERYTHING, Skill(None, 999, "x", {"nl": "X", "en": "X"}))
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id"):
        async with world.uow.locked() as tx:
            await tx.save_skill(EVERYTHING, Skill(999, world.acme, "x", {"nl": "X", "en": "X"}))


# --- staff grants -----------------------------------------------------------------------------


async def grant(
    world: World,
    staff_id: int,
    skills: Any,
    *,
    organisation: int | None = None,
    scope: Scope = EVERYTHING,
    by: int | None = None,
    at: datetime = NOW,
) -> None:
    """Replace a person's skills of an organisation (Acme by default)."""
    async with world.uow.locked() as tx:
        await tx.set_staff_skills(
            scope,
            staff_id,
            organisation or world.acme,
            skills,
            granted_by=by,
            at=at,
        )


async def test_staff_skills_are_replaced_as_a_set_and_keep_unchanged_grants(world: World) -> None:
    one, two, three = [await add_skill(world, c) for c in ("one", "two", "three")]
    assert one.id
    assert two.id
    assert three.id
    await grant(world, world.acme_staff, {one.id, two.id})
    await grant(world, world.acme_staff, [two.id, three.id], by=world.acme_staff, at=LATER)

    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(ACME, world.acme_staff) == {two.id, three.id}
        assert await tx.list_staff_skills(BETA, world.acme_staff) == frozenset()
        assert await tx.list_staff_skills(NOBODY, world.acme_staff) == frozenset()


async def test_a_kept_grant_keeps_who_granted_it(world: World) -> None:
    granted_by = who_granted(world)
    one, two = await add_skill(world, "one"), await add_skill(world, "two")
    assert one.id
    assert two.id
    await grant(world, world.acme_staff, {one.id})
    await grant(world, world.acme_staff, {one.id, two.id}, by=world.acme_staff, at=LATER)

    assert await granted_by(world.acme_staff, one.id) is None  # kept as granted first
    assert await granted_by(world.acme_staff, two.id) == world.acme_staff


async def test_a_staff_member_holds_no_skill_of_an_organisation_they_have_no_place_in(
    world: World,
) -> None:
    other = await add_skill(world, organisation="beta")
    assert other.id is not None

    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "skill_ids"):
        await grant(world, world.acme_staff, {other.id})
    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "organisation_id"):
        await grant(world, world.acme_staff, {other.id}, organisation=world.beta)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_ids"):
        await grant(world, world.acme_staff, {999})


async def test_an_archived_skill_is_not_granted_but_an_old_grant_stays_or_goes(
    world: World,
) -> None:
    held, new = await add_skill(world, "held"), await add_skill(world, "new")
    assert held.id
    assert new.id
    await grant(world, world.acme_staff, {held.id})
    async with world.uow.locked() as tx:
        await tx.archive_skill(EVERYTHING, held.id, NOW)
        await tx.archive_skill(EVERYTHING, new.id, NOW)

    with refused(ErrorCode.INVALID_CONFIGURATION, "skill_ids"):
        await grant(world, world.acme_staff, {held.id, new.id})
    await grant(world, world.acme_staff, {held.id}, at=LATER)  # keeping the archived grant is fine
    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(ACME, world.acme_staff) == {held.id}
    await grant(world, world.acme_staff, set(), at=LATER)  # and removing it is always allowed
    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(ACME, world.acme_staff) == frozenset()


async def test_setting_the_skills_of_one_organisation_leaves_the_others_alone(
    world: World,
) -> None:
    acme_skill = await add_skill(world, "a")
    beta_skill = await add_skill(world, "b", organisation="beta")
    assert acme_skill.id
    assert beta_skill.id
    await grant(world, world.both_staff, {acme_skill.id})
    await grant(world, world.both_staff, {beta_skill.id}, organisation=world.beta)

    await grant(world, world.both_staff, set())  # Acme's admin clears their Acme skills

    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(EVERYTHING, world.both_staff) == {beta_skill.id}
    await grant(world, world.both_staff, {acme_skill.id}, scope=ACME)
    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(EVERYTHING, world.both_staff) == {
            acme_skill.id,
            beta_skill.id,
        }
        assert await tx.list_staff_skills(BETA, world.both_staff) == {beta_skill.id}


async def test_a_grant_outside_the_scope_is_refused_like_a_missing_organisation(
    world: World,
) -> None:
    skill = await add_skill(world)
    assert skill.id is not None

    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "organisation_id"):
        await grant(world, world.acme_staff, {skill.id}, scope=BETA)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "organisation_id"):
        await grant(world, world.acme_staff, set(), scope=NOBODY)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "organisation_id"):
        await grant(world, world.acme_staff, {skill.id}, organisation=999)
    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(EVERYTHING, world.acme_staff) == frozenset()


async def test_a_grant_does_not_outlive_the_membership_for_eligibility(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    await grant(world, world.both_staff, {skill.id})
    async with world.uow.locked() as tx:  # the person leaves Acme
        await tx.save_staff(person("sub-ab", ["beta"]))

    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(EVERYTHING, world.both_staff) == frozenset()
    await grant(world, world.both_staff, set())  # removing a stale grant is still allowed
    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "organisation_id"):
        await grant(world, world.both_staff, {skill.id})


async def test_an_erased_or_unknown_staff_member_has_no_skills(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    await grant(world, world.acme_staff, {skill.id})
    async with world.uow.locked() as tx:
        await tx.set_staff_status("sub-a", StaffStatus.SUSPENDED, NOW)
        await tx.erase_staff("sub-a", LATER)

    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(EVERYTHING, world.acme_staff) == frozenset()
        assert await tx.skill_usage(EVERYTHING, skill.id) == SkillUsage()
    for staff_id in (world.acme_staff, 999):
        with refused(ErrorCode.STAFF_NOT_FOUND):
            await grant(world, staff_id, {skill.id}, at=LATER)


async def test_erasing_a_person_empties_who_published_a_revision(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    await grant(world, world.both_staff, {skill.id}, by=world.acme_staff)
    flow = await add_flow(world)
    assert flow.id is not None
    await publish(world, flow.id)  # published by Acme's admin, sub-a
    assert (await get_flow(world, flow.id)).current is not None

    async with world.uow.locked() as tx:
        await tx.set_staff_status("sub-a", StaffStatus.SUSPENDED, NOW)
        await tx.erase_staff("sub-a", LATER)

    stored = await get_flow(world, flow.id)
    assert stored.current is not None
    assert stored.current.published_by is None
    assert stored.current.published_at == NOW  # when it was published stays
    async with world.uow.read() as tx:
        assert await tx.list_staff_skills(EVERYTHING, world.both_staff) == {skill.id}


async def test_erasing_a_person_empties_who_granted_a_skill(world: World) -> None:
    granted_by = who_granted(world)
    skill = await add_skill(world)
    assert skill.id is not None
    await grant(world, world.both_staff, {skill.id}, by=world.acme_staff)
    assert await granted_by(world.both_staff, skill.id) == world.acme_staff

    async with world.uow.locked() as tx:
        await tx.set_staff_status("sub-a", StaffStatus.SUSPENDED, NOW)
        await tx.erase_staff("sub-a", LATER)

    assert await granted_by(world.both_staff, skill.id) is None


# --- capacity pools ---------------------------------------------------------------------------


async def test_a_pool_keeps_its_guaranteed_skills_and_replaces_them_on_update(world: World) -> None:
    one, two = await add_skill(world, "one"), await add_skill(world, "two")
    assert one.id
    assert two.id
    pool = await add_pool(world, skills=frozenset({one.id}))
    assert pool.id is not None
    assert pool.guaranteed_skill_ids == {one.id}

    async with world.uow.locked() as tx:
        await tx.save_capacity_pool(
            ACME, replace(pool, name="Kantoor", guaranteed_skill_ids={two.id})
        )
    async with world.uow.read() as tx:
        found = await tx.get_capacity_pool(ACME, pool.id)
        assert await tx.get_capacity_pool(BETA, pool.id) is None
        assert await tx.list_capacity_pools(BETA, world.acme) == []

    assert found is not None
    assert (found.name, found.guaranteed_skill_ids) == ("Kantoor", frozenset({two.id}))


async def test_a_pool_cannot_stop_guaranteeing_a_skill_a_flow_on_it_still_requires(
    world: World,
) -> None:
    one, two = await add_skill(world, "one"), await add_skill(world, "two")
    assert one.id
    assert two.id
    pool = await add_pool(world, skills=frozenset({one.id, two.id}))
    assert pool.id is not None
    flow = await add_flow(world, capacity_pool_id=pool.id, required_skill_ids=frozenset({one.id}))
    assert flow.id is not None
    subject = await add_flow(
        world,
        "b",
        capacity_pool_id=pool.id,
        subject_skill_ids={"id": frozenset({two.id})},
    )
    assert subject.id is not None

    with refused(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "guaranteed_skill_ids"):
        async with world.uow.locked() as tx:  # the flow requires it
            await tx.save_capacity_pool(ACME, replace(pool, guaranteed_skill_ids={two.id}))
    with refused(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "guaranteed_skill_ids"):
        async with world.uow.locked() as tx:  # a subject of the other flow requires it
            await tx.save_capacity_pool(ACME, replace(pool, guaranteed_skill_ids={one.id}))
    async with world.uow.locked() as tx:  # adding is always fine
        await tx.save_capacity_pool(ACME, replace(pool, name="More"))
    async with world.uow.locked() as tx:
        await tx.delete_service_flow(ACME, flow.id)
        await tx.delete_service_flow(ACME, subject.id)
        await tx.save_capacity_pool(ACME, replace(pool, guaranteed_skill_ids=frozenset()))
    async with world.uow.read() as tx:
        found = await tx.get_capacity_pool(ACME, pool.id)
    assert found is not None
    assert found.guaranteed_skill_ids == frozenset()


async def test_a_counted_pool_has_a_code_that_is_unique_per_organisation_and_fixed(
    world: World,
) -> None:
    pool = await add_pool(world, "Loket")
    assert pool.code == "loket"

    async def save(candidate: CapacityPool) -> CapacityPool:
        async with world.uow.locked() as tx:
            return await tx.save_capacity_pool(EVERYTHING, candidate)

    with refused(ErrorCode.SLUG_TAKEN, "pool_code"):
        await save(CapacityPool(None, world.acme, "Anders", PoolMode.COUNTED, code="loket"))
    other = await save(CapacityPool(None, world.beta, "Loket", PoolMode.COUNTED, code="loket"))
    assert other.code == "loket"  # another organisation may use the same code and name
    with refused(ErrorCode.INVALID_CONFIGURATION, "pool_code"):  # fixed after creation
        await save(replace(pool, code="anders"))
    renamed = await save(replace(pool, name="Kantoor"))
    assert (renamed.code, renamed.name) == ("loket", "Kantoor")
    await world.archive_pool_directly(pool.id or 0)
    with refused(ErrorCode.SLUG_TAKEN, "pool_code"):  # an archived pool keeps its code
        await save(CapacityPool(None, world.acme, "Nieuw", PoolMode.COUNTED, code="loket"))


async def test_a_counted_pool_name_is_unique_among_pools_not_archived(world: World) -> None:
    first = await add_pool(world, "Loket")
    assert first.id is not None

    async def save(candidate: CapacityPool) -> CapacityPool:
        async with world.uow.locked() as tx:
            return await tx.save_capacity_pool(EVERYTHING, candidate)

    def named(name: str, code: str) -> CapacityPool:
        return CapacityPool(None, world.acme, name, PoolMode.COUNTED, code=code)

    with refused(ErrorCode.SLUG_TAKEN, "pool_name"):
        await save(named("  LOKET ", "tweede"))
    second = await save(named("Spreekuur", "spreekuur"))
    with refused(ErrorCode.SLUG_TAKEN, "pool_name"):  # a rename onto another pool's name
        await save(replace(second, name="loket"))
    await save(replace(second, name="Spreekuur"))  # an unchanged name is not a clash
    await world.archive_pool_directly(first.id)
    again = await save(named("Loket", "loket-2"))  # an archived pool's name is free again
    assert again.name == "Loket"
    await save(CapacityPool(None, world.beta, "Loket", PoolMode.COUNTED, code="loket"))


async def test_a_legacy_pool_has_no_code_and_may_share_a_name(world: World) -> None:
    await add_pool(world, "Balie")
    async with world.uow.locked() as tx:
        legacy = await tx.save_capacity_pool(
            EVERYTHING,
            CapacityPool(
                None, world.acme, "Balie", PoolMode.LEGACY_GROUP, group_id=world.acme_group
            ),
        )
    assert legacy.code is None


async def test_a_pool_guarantees_only_own_unarchived_skills(world: World) -> None:
    other = await add_skill(world, organisation="beta")
    old = await add_skill(world, "old")
    assert other.id
    assert old.id
    async with world.uow.locked() as tx:
        await tx.archive_skill(EVERYTHING, old.id, NOW)

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
                EVERYTHING,
                CapacityPool(None, organisation, "Balie", PoolMode.LEGACY_GROUP, group_id=group),
            )

    pool = await legacy_pool(world.acme, world.acme_group)
    assert pool.group_id == world.acme_group

    with refused(ErrorCode.CONFIGURATION_OBJECT_EXISTS, "group_id"):
        await legacy_pool(world.acme, world.acme_group)
    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "group_id"):
        await legacy_pool(world.acme, world.beta_group)
    with refused(ErrorCode.INVALID_CONFIGURATION, "group_id"):
        await legacy_pool(world.acme, None)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "group_id"):
        await legacy_pool(world.acme, 999)
    with refused(ErrorCode.INVALID_CONFIGURATION, "mode"):  # mode and group never change
        async with world.uow.locked() as tx:
            await tx.save_capacity_pool(
                EVERYTHING, replace(pool, mode=PoolMode.COUNTED, group_id=None, code="balie")
            )


async def test_a_pool_of_another_organisation_cannot_be_overwritten(world: World) -> None:
    pool = await add_pool(world)

    with refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "organisation_id"):
        async with world.uow.locked() as tx:
            await tx.save_capacity_pool(EVERYTHING, replace(pool, organisation_id=world.beta))


async def test_a_pool_outside_the_scope_is_missing_to_every_method(world: World) -> None:
    pool = await add_pool(world, "Mine")
    assert pool.id is not None
    flow = await add_flow(world, capacity_pool_id=pool.id)
    assert flow.id is not None

    for scope in (BETA, NOBODY):
        with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id"):
            async with world.uow.locked() as tx:
                await tx.archive_capacity_pool(scope, pool.id, NOW)
        async with world.uow.read() as tx:
            assert await tx.flows_on_pool(scope, pool.id) == PoolFlows()
            assert await tx.flows_on_pool(scope, 999) == PoolFlows()  # the same as a missing id
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id"):
        async with world.uow.locked() as tx:  # claims Beta, names Acme's id
            await tx.save_capacity_pool(BETA, replace(pool, organisation_id=world.beta))
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "organisation_id"):
        async with world.uow.locked() as tx:
            await tx.save_capacity_pool(BETA, replace(pool, name="Taken over"))
    async with world.uow.read() as tx:
        assert await tx.flows_on_pool(ACME, pool.id) == PoolFlows((flow.id,))
        (unchanged,) = await tx.list_capacity_pools(EVERYTHING, world.acme)
    assert (unchanged.name, unchanged.archived_at) == ("Mine", None)


async def test_a_pool_that_a_flow_draws_on_cannot_be_archived(world: World) -> None:
    pool = await add_pool(world)
    assert pool.id is not None
    draft = await add_flow(world, capacity_pool_id=pool.id)
    assert draft.id is not None
    async with world.uow.read() as tx:
        assert await tx.flows_on_pool(ACME, pool.id) == PoolFlows((draft.id,))
    with refused(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "capacity_pool"):  # a draft holds it
        async with world.uow.locked() as tx:
            await tx.archive_capacity_pool(ACME, pool.id, NOW)
    async with world.uow.locked() as tx:  # once the draft flow is gone it can go
        await tx.delete_service_flow(ACME, draft.id)
        await tx.archive_capacity_pool(ACME, pool.id, NOW)
    async with world.uow.read() as tx:
        assert await tx.list_capacity_pools(ACME, world.acme) == []
        (archived,) = await tx.list_capacity_pools(ACME, world.acme, archived=True)
    assert archived.archived_at == NOW

    busy = await add_pool(world, "Busy")
    assert busy.id is not None
    flow = await add_flow(world, "b", capacity_pool_id=busy.id)
    assert flow.id is not None
    await activate(world, flow.id)
    with refused(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "capacity_pool"):
        async with world.uow.locked() as tx:
            await tx.archive_capacity_pool(ACME, busy.id, NOW)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id"):
        async with world.uow.locked() as tx:
            await tx.archive_capacity_pool(EVERYTHING, 999, NOW)


async def test_flows_on_a_pool_are_the_flows_not_archived_by_current_or_draft(
    world: World,
) -> None:
    first, second = await add_pool(world, "First"), await add_pool(world, "Second")
    assert first.id
    assert second.id
    flow = await add_flow(world, capacity_pool_id=first.id)
    assert flow.id is not None
    await activate(world, flow.id)
    await save_draft(
        world,
        replace(flow, draft=revision(flow_id=flow.id, capacity_pool_id=second.id)),
    )

    async with world.uow.read() as tx:
        assert (await tx.flows_on_pool(ACME, first.id)).flows == (flow.id,)  # the published one
        assert (await tx.flows_on_pool(ACME, second.id)).flows == (flow.id,)  # the draft
    async with world.uow.locked() as tx:
        await tx.set_flow_state(EVERYTHING, flow.id, FlowState.ARCHIVED)
    async with world.uow.read() as tx:
        assert await tx.flows_on_pool(ACME, first.id) == PoolFlows()
        assert await tx.flows_on_pool(ACME, second.id) == PoolFlows()


async def test_a_pool_cannot_be_archived_while_a_draft_or_a_current_revision_draws_on_it(
    world: World,
) -> None:
    live, planned = await add_pool(world, "Live"), await add_pool(world, "Planned")
    assert live.id
    assert planned.id
    flow = await add_flow(world, capacity_pool_id=live.id)
    assert flow.id is not None
    published = await activate(world, flow.id)
    await save_draft(
        world, replace(published, draft=revision(flow_id=flow.id, capacity_pool_id=planned.id))
    )

    for pool in (live, planned):  # the published revision holds one, the draft the other
        with refused(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "capacity_pool"):
            async with world.uow.locked() as tx:
                await tx.archive_capacity_pool(EVERYTHING, pool.id, NOW)
    async with world.uow.locked() as tx:  # an archived flow holds nothing
        await tx.set_flow_state(EVERYTHING, flow.id, FlowState.ARCHIVED)
        await tx.archive_capacity_pool(EVERYTHING, live.id, NOW)
        await tx.archive_capacity_pool(EVERYTHING, planned.id, NOW)


async def test_a_pool_of_a_flow_in_another_group_still_blocks_a_group_scoped_archive(
    world: World,
) -> None:
    pool = await add_pool(world)
    assert pool.id is not None
    async with world.uow.locked() as tx:
        other = await tx.add_group(make_group(id=0, organisation_id=world.acme, slug="kantoor"))
        await tx.save_draft(EVERYTHING, blank(world, "x", other.id, capacity_pool_id=pool.id))

    with refused(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "capacity_pool"):
        async with world.uow.locked() as tx:
            await tx.archive_capacity_pool(ACME_GROUP, pool.id, NOW)


async def test_the_flows_on_a_pool_follow_the_callers_groups_and_count_the_rest(
    world: World,
) -> None:
    pool = await add_pool(world)
    assert pool.id is not None
    mine = await add_flow(world, "mine", capacity_pool_id=pool.id)
    async with world.uow.locked() as tx:
        other = await tx.add_group(make_group(id=0, organisation_id=world.acme, slug="kantoor"))
        elsewhere = [
            await tx.save_draft(EVERYTHING, blank(world, code, other.id, capacity_pool_id=pool.id))
            for code in ("x", "y")
        ]
        await tx.save_draft(EVERYTHING, blank(world, "unrelated", other.id))
    assert mine.id is not None
    flows = (mine.id, *(f.id for f in elsewhere))

    async with world.uow.read() as tx:
        assert await tx.flows_on_pool(ACME, pool.id) == PoolFlows(flows, 0)
        assert await tx.flows_on_pool(EVERYTHING, pool.id) == PoolFlows(flows, 0)
        assert await tx.flows_on_pool(ACME_GROUP, pool.id) == PoolFlows((mine.id,), 2)


# --- flows ------------------------------------------------------------------------------------


async def test_a_new_flow_is_a_draft_with_revision_one_and_round_trips(world: World) -> None:
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
    assert stored.state is FlowState.DRAFT
    assert stored.current is None
    assert stored.draft is not None
    assert (stored.draft.flow_id, stored.draft.revision) == (stored.id, 1)
    assert stored.draft.published_at is None
    async with world.uow.read() as tx:
        listed = await tx.list_service_flows(ACME, world.acme_group)
        found = await tx.get_service_flow(EVERYTHING, stored.id)
        revision_one = await tx.flow_revision(stored.id, 1)
        missing = await tx.flow_revision(stored.id, 2)

    assert listed == [stored]
    assert found == stored
    assert revision_one == stored.draft
    assert missing is None
    assert stored.draft.subject_skill_ids == {"id": frozenset({skill.id}), "visa": frozenset()}


async def test_a_flow_outside_the_scope_is_invisible_and_cannot_be_changed(world: World) -> None:
    flow = await add_flow(world)
    assert flow.id is not None

    async with world.uow.read() as tx:
        assert await tx.get_service_flow(BETA, flow.id) is None
        assert await tx.get_service_flow(NOBODY, flow.id) is None
        assert await tx.list_service_flows(BETA, world.acme_group) == []
        assert await tx.list_service_flows(ACME_GROUP, world.acme_group) == [flow]
        assert await tx.list_service_flows(ACME, world.beta_group) == []
    changed = replace(
        flow, draft=revision(flow_id=flow.id, capacity_pool_id=None, duration_minutes=5)
    )
    with refused(ErrorCode.GROUP_NOT_FOUND):
        await save_draft(world, changed, BETA)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id"):  # right group, wrong flow
        await save_draft(world, replace(changed, group_id=world.beta_group), BETA)
    for method in (
        lambda tx: tx.publish_draft(BETA, flow.id, by=world.acme_staff, at=NOW),
        lambda tx: tx.discard_draft(BETA, flow.id),
        lambda tx: tx.set_flow_state(BETA, flow.id, FlowState.ACTIVE),
    ):
        with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id"):
            async with world.uow.locked() as tx:
                await method(tx)
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id"):
        async with world.uow.locked() as tx:
            await tx.discard_draft(EVERYTHING, 999)  # the same as one outside the scope
    assert await get_flow(world, flow.id) == flow  # nothing changed


async def test_saving_a_draft_again_overwrites_its_unpublished_revision(world: World) -> None:
    skill = await add_skill(world)
    assert skill.id is not None
    stored = await add_flow(
        world,
        required_skill_ids=frozenset({skill.id}),
        subject_skill_ids={"id": frozenset({skill.id})},
    )
    assert stored.id is not None

    changed = replace(
        stored,
        code="b",
        default_for=frozenset({IntakeMode.APPOINTMENT}),
        draft=revision(
            flow_id=stored.id,
            capacity_pool_id=None,
            duration_minutes=45,
            subject_skill_ids={"visa": frozenset()},
        ),
    )
    again = await save_draft(world, changed, ACME)
    async with world.uow.read() as tx:
        listed = await tx.list_service_flows(ACME, world.acme_group)

    assert listed == [again]
    assert again.current is None
    assert again.draft is not None
    assert (again.code, again.draft.revision, again.draft.duration_minutes) == ("b", 1, 45)
    assert again.default_for == {IntakeMode.APPOINTMENT}
    assert again.draft.required_skill_ids == frozenset()
    assert again.draft.subject_skill_ids == {"visa": frozenset()}


async def test_a_flow_code_is_unique_per_group(world: World) -> None:
    await add_flow(world, "a")
    other = await add_flow(world, "b")

    with refused(ErrorCode.SLUG_TAKEN, "flow_code"):
        await add_flow(world, "a")
    with refused(ErrorCode.SLUG_TAKEN, "flow_code"):
        await save_draft(world, replace(other, code="a"))
    async with world.uow.locked() as tx:  # the same code in another group is fine
        await tx.save_draft(EVERYTHING, blank(world, "a", group=world.beta_group))


async def test_publishing_makes_the_draft_current_and_stamps_who_and_when(world: World) -> None:
    flow = await add_flow(world)
    assert flow.id is not None

    published = await publish(world, flow.id, LATER)

    assert published.draft is None
    assert published.state is FlowState.DRAFT  # activating is a separate step
    assert published.current is not None
    assert (published.current.revision, published.current.published_at) == (1, LATER)
    assert published.current.published_by == world.acme_staff
    async with world.uow.read() as tx:
        assert await tx.get_service_flow(EVERYTHING, flow.id) == published
        assert await tx.flow_revision(flow.id, 1) == published.current
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "draft"):
        await publish(world, flow.id)


async def test_an_active_flow_is_edited_by_a_draft_next_to_its_published_revision(
    world: World,
) -> None:
    flow = await add_flow(world)
    assert flow.id is not None
    live = await activate(world, flow.id)
    assert live.state is FlowState.ACTIVE
    assert live.current is not None
    assert live.draft is None

    def draft(minutes: int) -> ServiceFlow:
        return replace(
            live,
            draft=revision(flow_id=flow.id, capacity_pool_id=None, duration_minutes=minutes),
        )

    first = await save_draft(world, draft(45))
    second = await save_draft(world, draft(50))  # overwrites the draft, still revision 2
    async with world.uow.read() as tx:
        old = await tx.flow_revision(flow.id, 1)
        new = await tx.flow_revision(flow.id, 2)

    assert first.draft is not None
    assert first.draft.revision == 2
    assert second.draft is not None
    assert (second.draft.revision, second.draft.duration_minutes) == (2, 50)
    assert second.current == live.current  # the published revision is never changed
    assert second.state is FlowState.ACTIVE
    assert old == live.current
    assert old is not None
    assert (old.duration_minutes, old.published_at) == (20, NOW)
    assert new == second.draft

    published = await publish(world, flow.id, LATER)
    assert published.current is not None
    assert (published.current.revision, published.current.duration_minutes) == (2, 50)
    assert published.draft is None
    assert (await get_flow(world, flow.id)).state is FlowState.ACTIVE
    async with world.uow.read() as tx:
        assert await tx.flow_revision(flow.id, 1) == live.current  # still there for old bookings
    third = await save_draft(world, draft(55))  # the next draft is revision 3
    assert third.draft is not None
    assert third.draft.revision == 3


async def test_discarding_a_draft_keeps_the_published_revision_and_refuses_a_flow_never_published(
    world: World,
) -> None:
    live = await add_flow(world, "live")
    fresh = await add_flow(world, "fresh")
    assert live.id is not None
    assert fresh.id is not None
    published = await activate(world, live.id)
    await save_draft(
        world,
        replace(published, draft=revision(flow_id=live.id, capacity_pool_id=None, revision=2)),
    )

    async with world.uow.locked() as tx:
        await tx.discard_draft(EVERYTHING, live.id)

    assert await get_flow(world, live.id) == published
    async with world.uow.read() as tx:
        assert await tx.flow_revision(live.id, 2) is None
        assert await tx.flow_revision(fresh.id, 1) is not None
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "draft"):
        async with world.uow.locked() as tx:
            await tx.discard_draft(EVERYTHING, live.id)
    with refused(ErrorCode.INVALID_CONFIGURATION, "state"):  # that flow is deleted, not discarded
        async with world.uow.locked() as tx:
            await tx.discard_draft(EVERYTHING, fresh.id)
    again = await save_draft(
        world,
        replace(published, draft=revision(flow_id=live.id, capacity_pool_id=None)),
    )
    assert again.draft is not None


async def test_a_flow_never_active_is_deleted_with_all_its_revisions_and_rows(world: World) -> None:
    skill = await add_skill(world, "one")
    assert skill.id is not None
    pool = await add_pool(world, skills=frozenset({skill.id}))
    assert pool.id is not None
    flow = await add_flow(
        world,
        "gone",
        capacity_pool_id=pool.id,
        required_skill_ids=frozenset({skill.id}),
        subject_skill_ids={"id": frozenset({skill.id})},
    )
    assert flow.id is not None
    await publish(world, flow.id)
    await save_draft(
        world,
        replace(
            await get_flow(world, flow.id),
            draft=revision(flow_id=flow.id, capacity_pool_id=None, revision=2),
        ),
    )

    async with world.uow.locked() as tx:
        await tx.delete_service_flow(ACME, flow.id)

    async with world.uow.read() as tx:
        assert await tx.get_service_flow(EVERYTHING, flow.id) is None
        assert [await tx.flow_revision(flow.id, n) for n in (1, 2)] == [None, None]
        assert await tx.flows_on_pool(ACME, pool.id) == PoolFlows()
        assert (await tx.skill_usage(ACME, skill.id)).flows == 0
    with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id"):
        async with world.uow.locked() as tx:
            await tx.delete_service_flow(ACME, flow.id)


async def test_deleting_a_flow_is_scoped_and_refused_for_one_that_is_or_was_active(
    world: World,
) -> None:
    mine = await add_flow(world, "mine")
    live = await add_flow(world, "live")
    assert mine.id is not None
    assert live.id is not None
    await activate(world, live.id)

    for scope in (BETA, NOBODY):  # another organisation's flow is a missing one
        with refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id"):
            async with world.uow.locked() as tx:
                await tx.delete_service_flow(scope, mine.id)
    with refused(ErrorCode.INVALID_CONFIGURATION, "state"):  # it is active
        async with world.uow.locked() as tx:
            await tx.delete_service_flow(ACME, live.id)
    async with world.uow.locked() as tx:
        await tx.set_flow_state(ACME, live.id, FlowState.ARCHIVED)
    with refused(ErrorCode.INVALID_CONFIGURATION, "state"):  # it was active
        async with world.uow.locked() as tx:
            await tx.delete_service_flow(ACME, live.id)
    async with world.uow.locked() as tx:
        await tx.delete_service_flow(ACME, mine.id)
