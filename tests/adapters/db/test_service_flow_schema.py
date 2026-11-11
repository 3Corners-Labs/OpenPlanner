"""What the schema of skills, pools and flows enforces itself (ADR 0121 step 2b)."""

import pytest
from sqlalchemy import func, select
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
from tests.adapters.db.conftest import seed_group


async def _group_and_organisation(engine: AsyncEngine) -> tuple[int, int]:
    """Seed a group and return its id and its organisation's id."""
    group_id = await seed_group(engine)
    async with AsyncSession(engine) as session:
        organisation = await session.scalar(
            select(GroupRow.organisation_id).where(GroupRow.id == group_id)
        )
    assert organisation is not None
    return group_id, organisation


def _flow(group_id: int, code: str, *, state: str = "active", **defaults: bool) -> ServiceFlowRow:
    return ServiceFlowRow(group_id=group_id, code=code, state=state, **defaults)


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
        flow.current_revision = 1
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
