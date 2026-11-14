"""The flow examples of the e2e seed (ADR 0121 step 2d): skills, a pool, draft flows, a subject."""

import dataclasses
import json
import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import (
    FLOW_EXAMPLE_FLOW,
    FLOW_EXAMPLE_POOL,
    FLOW_EXAMPLE_SKILLS,
    FLOW_EXAMPLE_SUBJECT,
    FLOW_EXAMPLE_SUBJECT_FLOW,
    FLOW_GROUP_SLUG,
    ClosureExamples,
    FlowExamples,
    seed_closure_examples,
    seed_demo,
    seed_flow_examples,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.service_flows import FlowState, InteractionKind
from tests.support.domain import NOW

PAGES = json.loads(Path("e2e/pages.json").read_text())
PAGES_TS = Path("e2e/pages.ts").read_text()
SETUP_KEYS = re.compile(r"const SETUP_KEYS[^=]*= new Set\(\[([^\]]*)\]\)")


async def test_the_seed_adds_the_skills_pool_and_draft_flows_once(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    first = await seed_flow_examples(engine)
    second = await seed_flow_examples(engine)
    async with SqlUnitOfWork(engine).read() as tx:
        everything = Scope(everything=True)
        organisation = await tx.get_organisation("demo")
        assert organisation is not None
        skills = await tx.list_skills(everything, organisation.id)
        pools = await tx.list_capacity_pools(everything, organisation.id)
        group = await tx.get_group("demo")
        other = await tx.get_group(FLOW_GROUP_SLUG)
        assert group is not None
        assert other is not None
        flows = await tx.list_service_flows(everything, group.id)
        subject_flows = await tx.list_service_flows(everything, other.id)

    assert sorted(s.code for s in skills) == sorted(code for code, _, _ in FLOW_EXAMPLE_SKILLS)
    assert [p.code for p in pools if p.code] == [FLOW_EXAMPLE_POOL]
    (draft,) = [f for f in flows if f.code == FLOW_EXAMPLE_FLOW]
    assert draft.state is FlowState.DRAFT
    assert draft.draft is not None
    assert draft.draft.interaction_kind is InteractionKind.NONE
    (offering,) = [f for f in subject_flows if f.code == FLOW_EXAMPLE_SUBJECT_FLOW]
    assert offering.draft is not None
    assert [s.code for s in other.routing.subjects] == [FLOW_EXAMPLE_SUBJECT]
    assert set(offering.draft.subject_skill_ids) == {FLOW_EXAMPLE_SUBJECT}
    assert first == second
    assert first.skill_archive == f"{first.skill_edit}/archive"
    assert first.pool_archive == f"{first.pool_edit}/archive"
    assert first.flow_subject.endswith(f"/flows/{FLOW_EXAMPLE_SUBJECT_FLOW}/subjects/aanvraag")


async def test_every_placeholder_of_the_pages_list_is_a_path_the_seed_command_writes(
    engine: AsyncEngine,
) -> None:
    """The resolver (``variants`` in ``e2e/pages.ts``) takes the keys of two files.

    ``seed-closures`` merges the closure and the flow examples and the immediate form into
    ``fixtures.json``; the global set-up ``e2e/requests.ts`` writes ``requests.json`` (its keys
    are ``SETUP_KEYS`` in ``e2e/pages.ts``). A placeholder that names none of them would fail
    the page checks only when they run, so it fails here first.
    """
    await seed_demo(engine)
    written = dataclasses.asdict(
        await seed_closure_examples(engine, NOW, b"k" * 32)
    ) | dataclasses.asdict(await seed_flow_examples(engine))
    pages = PAGES
    placeholders = {
        match.group(1)
        for page in [*pages["pages"], *pages["admin"]]
        for match in re.finditer(r"\{(\w+)\}", page["path"])
    }

    assert placeholders
    keys = SETUP_KEYS.search(PAGES_TS)
    assert keys is not None
    set_up = set(re.findall(r"'(\w+)'", keys.group(1)))
    assert "immediate_request" in set_up
    assert placeholders <= set(written) | {"immediate_form"} | set_up
    assert all(
        re.fullmatch(r"\{\w+\}", p["path"])
        for p in [*pages["pages"], *pages["admin"]]
        if "{" in p["path"]
    )
    assert {"skill_edit", "pool_edit", "flow_subject"} <= placeholders
    assert {f.name for f in dataclasses.fields(FlowExamples)}.isdisjoint(
        {f.name for f in dataclasses.fields(ClosureExamples)}
    )
