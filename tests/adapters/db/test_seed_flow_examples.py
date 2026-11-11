"""The flow examples of the e2e seed (ADR 0121 step 2d): two skills, one pool, one draft flow."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import (
    FLOW_EXAMPLE_FLOW,
    FLOW_EXAMPLE_POOL,
    FLOW_EXAMPLE_SKILLS,
    seed_closure_examples,
    seed_demo,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.service_flows import FlowState, InteractionKind
from tests.support.domain import NOW


async def test_the_seed_adds_the_skills_pool_and_draft_flow_once(engine: AsyncEngine) -> None:
    key = b"k" * 32
    await seed_demo(engine)
    first = await seed_closure_examples(engine, NOW, key)
    second = await seed_closure_examples(engine, NOW, key)
    async with SqlUnitOfWork(engine).read() as tx:
        everything = Scope(everything=True)
        organisation = await tx.get_organisation("demo")
        assert organisation is not None
        skills = await tx.list_skills(everything, organisation.id)
        pools = await tx.list_capacity_pools(everything, organisation.id)
        group = await tx.get_group("demo")
        assert group is not None
        flows = await tx.list_service_flows(everything, group.id)

    assert sorted(s.code for s in skills) == sorted(code for code, _, _ in FLOW_EXAMPLE_SKILLS)
    assert [p.code for p in pools if p.code] == [FLOW_EXAMPLE_POOL]
    (draft,) = [f for f in flows if f.code == FLOW_EXAMPLE_FLOW]
    assert draft.state is FlowState.DRAFT
    assert draft.draft is not None
    assert draft.draft.interaction_kind is InteractionKind.NONE
    assert (first.skill_edit, first.pool_edit) == (second.skill_edit, second.pool_edit)
    assert first.skill_archive == f"{first.skill_edit}/archive"
    assert first.pool_archive == f"{first.pool_edit}/archive"


@pytest.mark.parametrize("name", ["skill_edit", "pool_edit"])
def test_the_pages_list_names_the_new_fixtures(name: str) -> None:
    import json
    from pathlib import Path

    listed = json.loads(Path("e2e/pages.json").read_text())["admin"]

    assert "{" + name + "}" in {p["path"] for p in listed}
