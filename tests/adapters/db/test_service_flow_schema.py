"""What the schema of skills, pools and flows enforces itself (ADR 0121 step 2b)."""

from typing import Any

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import (
    CapacityPoolRow,
    FlowRevisionRow,
    FlowRevisionSkillRow,
    GroupRow,
    ServiceFlowRow,
    SkillRow,
    SubjectFlowRow,
    SubjectFlowSkillRow,
)
from app.adapters.db.uow import SqlUnitOfWork
from tests.support.seed_rows import seed_group


async def _group_and_organisation(engine: AsyncEngine) -> tuple[int, int]:
    """Seed a group and return its id and its organisation's id."""
    group_id = await seed_group(engine)
    async with AsyncSession(engine) as session:
        organisation = await session.scalar(
            select(GroupRow.organisation_id).where(GroupRow.id == group_id)
        )
    assert organisation is not None
    return group_id, organisation


def _flow(group_id: int, code: str, *, state: str = "active", **columns: Any) -> ServiceFlowRow:
    """Return a flow row; an active one names revision 1 as current, as the schema demands."""
    if state == "active":
        columns.setdefault("current_revision", 1)
    return ServiceFlowRow(group_id=group_id, code=code, state=state, **columns)


async def test_one_active_default_flow_per_entry_path_and_group(engine: AsyncEngine) -> None:
    group_id, _ = await _group_and_organisation(engine)
    async with AsyncSession(engine) as session, session.begin():
        session.add_all(
            [
                _flow(group_id, "one", default_appointment=True),
                _flow(group_id, "two", default_on_demand=True),  # the other path is free
                _flow(group_id, "draft", state="draft", default_appointment=True),  # not active
                _flow(group_id, "old", state="archived", default_appointment=True),
            ]
        )

    with pytest.raises(IntegrityError):
        async with AsyncSession(engine) as session, session.begin():
            session.add(_flow(group_id, "three", default_appointment=True))
    with pytest.raises(IntegrityError):
        async with AsyncSession(engine) as session, session.begin():
            session.add(_flow(group_id, "four", default_on_demand=True))


async def test_a_flow_code_is_unique_in_its_group_in_the_schema_too(engine: AsyncEngine) -> None:
    group_id, _ = await _group_and_organisation(engine)
    async with AsyncSession(engine) as session, session.begin():
        session.add(_flow(group_id, "a"))

    with pytest.raises(IntegrityError):
        async with AsyncSession(engine) as session, session.begin():
            session.add(_flow(group_id, "a"))


async def test_a_pool_is_legacy_exactly_when_it_names_a_group(engine: AsyncEngine) -> None:
    group_id, organisation = await _group_and_organisation(engine)

    def pool(mode: str, group: int | None) -> CapacityPoolRow:
        return CapacityPoolRow(organisation_id=organisation, name="P", mode=mode, group_id=group)

    async with AsyncSession(engine) as session, session.begin():
        session.add_all([pool("legacy_group", group_id), pool("counted", None)])
    for bad in (pool("legacy_group", None), pool("counted", group_id), pool("other", None)):
        with pytest.raises(IntegrityError):
            async with AsyncSession(engine) as session, session.begin():
                session.add(bad)
    with pytest.raises(IntegrityError):  # a group has at most one legacy pool
        async with AsyncSession(engine) as session, session.begin():
            session.add(pool("legacy_group", group_id))


async def test_deleting_a_group_removes_its_pool_and_flows_with_everything_under_them(
    engine: AsyncEngine,
) -> None:
    group_id, organisation = await _group_and_organisation(engine)
    async with AsyncSession(engine) as session, session.begin():
        skill = SkillRow(organisation_id=organisation, code="s", labels="{}")
        pool = CapacityPoolRow(
            organisation_id=organisation, name="P", mode="legacy_group", group_id=group_id
        )
        flow = _flow(group_id, "a")
        session.add_all([skill, pool, flow])
        await session.flush()
        session.add(FlowRevisionRow(flow_id=flow.id, revision=1, labels="{}", intake_modes="x"))
        await session.flush()
        session.add_all(
            [
                FlowRevisionSkillRow(flow_id=flow.id, revision=1, skill_id=skill.id),
                SubjectFlowRow(flow_id=flow.id, revision=1, subject_code="id"),
            ]
        )
        await session.flush()
        session.add(
            SubjectFlowSkillRow(flow_id=flow.id, revision=1, subject_code="id", skill_id=skill.id)
        )
        flow.current_revision = 1  # a flow never published has NULL here and a draft revision
        revision = await session.get(FlowRevisionRow, (flow.id, 1))
        assert revision is not None
        revision.capacity_pool_id = pool.id

    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.delete_group(group_id)

    async with AsyncSession(engine) as session:
        for row in (CapacityPoolRow, ServiceFlowRow, FlowRevisionRow, SubjectFlowSkillRow):
            assert await session.scalar(select(func.count()).select_from(row)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(SkillRow)) == 1
        )  # the catalogue stays


async def test_a_revision_skill_must_name_an_existing_revision(engine: AsyncEngine) -> None:
    group_id, organisation = await _group_and_organisation(engine)
    async with AsyncSession(engine) as session, session.begin():
        skill = SkillRow(organisation_id=organisation, code="s", labels="{}")
        flow = _flow(group_id, "a")
        session.add_all([skill, flow])
        await session.flush()
        flow_id, skill_id = flow.id, skill.id

    with pytest.raises(IntegrityError):
        async with AsyncSession(engine) as session, session.begin():
            session.add(FlowRevisionSkillRow(flow_id=flow_id, revision=9, skill_id=skill_id))


async def test_a_flow_may_have_no_current_revision_and_a_draft_revision(
    engine: AsyncEngine,
) -> None:
    group_id, _ = await _group_and_organisation(engine)
    async with AsyncSession(engine) as session, session.begin():
        flow = _flow(group_id, "new", state="draft", draft_revision=1)
        session.add(flow)
    async with AsyncSession(engine) as session:
        stored = await session.scalar(select(ServiceFlowRow).where(ServiceFlowRow.code == "new"))
    assert stored is not None
    assert (stored.current_revision, stored.draft_revision) == (None, 1)


async def test_a_pool_that_a_revision_draws_on_cannot_be_deleted(engine: AsyncEngine) -> None:
    """Pools are archived, never deleted, and a published revision must not change."""
    group_id, organisation = await _group_and_organisation(engine)
    async with AsyncSession(engine) as session, session.begin():
        pool = CapacityPoolRow(organisation_id=organisation, name="P", mode="counted")
        flow = _flow(group_id, "a")
        session.add_all([pool, flow])
        await session.flush()
        session.add(
            FlowRevisionRow(
                flow_id=flow.id,
                revision=1,
                labels="{}",
                intake_modes="x",
                capacity_pool_id=pool.id,
            )
        )
        pool_id = pool.id

    with pytest.raises(IntegrityError):
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(delete(CapacityPoolRow).where(CapacityPoolRow.id == pool_id))


async def _organisation_with_a_pool_in_use(engine: AsyncEngine) -> tuple[int, int, int]:
    """Seed a counted pool used by a flow's current revision and another flow's draft.

    Returns the ids of the organisation, the group and the pool.
    """
    group_id, organisation = await _group_and_organisation(engine)
    async with AsyncSession(engine) as session, session.begin():
        pool = CapacityPoolRow(organisation_id=organisation, name="P", mode="counted")
        live = _flow(group_id, "live")
        fresh = _flow(group_id, "fresh", state="draft")
        session.add_all([pool, live, fresh])
        await session.flush()
        live.current_revision = 1
        fresh.draft_revision = 1
        session.add_all(
            FlowRevisionRow(
                flow_id=flow.id, revision=1, labels="{}", intake_modes="x", capacity_pool_id=pool.id
            )
            for flow in (live, fresh)
        )
        pool_id = pool.id
    return organisation, group_id, pool_id


async def _counts(engine: AsyncEngine) -> tuple[int, int, int]:
    """Return how many pools, flows and revisions are stored."""
    async with AsyncSession(engine) as session:
        pools = await session.scalar(select(func.count()).select_from(CapacityPoolRow))
        flows = await session.scalar(select(func.count()).select_from(ServiceFlowRow))
        revisions = await session.scalar(select(func.count()).select_from(FlowRevisionRow))
    return pools or 0, flows or 0, revisions or 0


async def test_deleting_an_organisation_after_its_groups_removes_a_pool_in_use(
    engine: AsyncEngine,
) -> None:
    """The organisation delete use case and the configuration import clear the groups first."""
    organisation, group_id, _ = await _organisation_with_a_pool_in_use(engine)

    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.delete_group(group_id)  # the flows and their revisions go with the group
        await tx.delete_organisation(organisation)

    assert await _counts(engine) == (0, 0, 0)


async def test_a_plain_delete_removes_pool_and_revisions_in_one_statement(
    engine: AsyncEngine,
) -> None:
    """Pool and revisions go in one cascade whatever the order: checked at statement end."""
    organisation, group_id, _ = await _organisation_with_a_pool_in_use(engine)
    async with AsyncSession(engine) as session, session.begin():
        legacy = CapacityPoolRow(
            organisation_id=organisation, name="L", mode="legacy_group", group_id=group_id
        )
        session.add(legacy)
        await session.flush()
        flow = await session.scalar(select(ServiceFlowRow).where(ServiceFlowRow.code == "live"))
        assert flow is not None
        revision = await session.get(FlowRevisionRow, (flow.id, 1))
        assert revision is not None
        revision.capacity_pool_id = legacy.id  # the group's own pool, deleted with the group

    async with AsyncSession(engine) as session, session.begin():
        await session.execute(text("DELETE FROM groups WHERE id = :id"), {"id": group_id})
        await session.execute(
            text("DELETE FROM organisations WHERE id = :id"), {"id": organisation}
        )

    assert await _counts(engine) == (0, 0, 0)


async def test_deleting_a_group_keeps_the_organisations_pool(engine: AsyncEngine) -> None:
    _, group_id, _ = await _organisation_with_a_pool_in_use(engine)

    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.delete_group(group_id)

    assert await _counts(engine) == (1, 0, 0)


async def test_a_flow_must_be_active_with_a_current_revision(engine: AsyncEngine) -> None:
    group_id, _ = await _group_and_organisation(engine)
    async with AsyncSession(engine) as session, session.begin():
        session.add(_flow(group_id, "live"))
        session.add(_flow(group_id, "new", state="draft"))  # a draft needs none

    with pytest.raises(IntegrityError):
        async with AsyncSession(engine) as session, session.begin():
            session.add(_flow(group_id, "broken", current_revision=None))
