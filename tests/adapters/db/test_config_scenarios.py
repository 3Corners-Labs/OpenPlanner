"""Prepared closure scenarios in a configuration document: read and write."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.config_scenarios import WRITERS, read_scenarios
from app.adapters.db.models import ClosureImageRow, ClosureScenarioRow
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Entity
from app.domain.configuration_values import Binary, Value
from app.domain.errors import DomainError, ErrorCode
from tests.adapters.db.conftest import seed_group
from tests.support.config_changes import apply_changes, planned
from tests.support.config_roundtrip import document_of, organisations, plan_against
from tests.support.images import png

ORG, GROUP = "org-aangifte", "aangifte"
NOTICES = {"nl": "Wegens een storing zijn wij gesloten.", "en": "Closed because of an outage."}
CONTACTS = {
    "headings": {"nl": "Bel ons"},
    "options": [{"kind": "phone", "value": "14 020", "labels": {}, "hours": {}}],
}
SCENARIO: dict[str, Value] = {
    "notices": NOTICES,
    "headings": {"nl": "Storing"},
    "contacts": CONTACTS,
}
IMAGE: dict[str, Value] = {
    "data": Binary("image/png", png(metadata=False)),
    "alts": {"nl": "Gesloten", "en": "Closed"},
    "decorative": False,
}


def change(
    kind: str,
    name: str,
    action: ChangeAction,
    values: dict[str, Value],
    *fields: str,
    group: str | None = None,
) -> Change:
    address = (ORG, name) if group is None else (ORG, group, name)
    key = () if kind == "scenario_image" else (name,)
    return planned(
        kind, address, action, values, fields or None, key=key, organisation=ORG, group=group
    )


def organisation_scenario(
    name: str, action: ChangeAction, values: dict[str, Value], *fields: str
) -> Change:
    return change("organisation_scenario", name, action, values, *fields)


def group_scenario(
    name: str, action: ChangeAction, values: dict[str, Value], *fields: str
) -> Change:
    return change("group_scenario", name, action, values, *fields, group=GROUP)


def image(
    name: str,
    action: ChangeAction,
    values: dict[str, Value],
    *fields: str,
    group: str | None = None,
) -> Change:
    return change("scenario_image", name, action, values, *fields, group=group)


async def apply(engine: AsyncEngine, *changes: Change) -> None:
    await apply_changes(engine, WRITERS, None, *changes)


async def read(
    engine: AsyncEngine,
) -> tuple[dict[str, tuple[Entity, ...]], dict[tuple[str, str], tuple[Entity, ...]]]:
    async with AsyncSession(engine) as session:
        return await read_scenarios(session)


@pytest.fixture
async def installation(engine: AsyncEngine) -> AsyncEngine:
    await seed_group(engine)
    return engine


async def seed(engine: AsyncEngine) -> None:
    await apply(
        engine,
        organisation_scenario("Storing", ChangeAction.CREATE, SCENARIO),
        image("Storing", ChangeAction.CREATE, IMAGE),
        group_scenario("Eigen", ChangeAction.CREATE, {**SCENARIO, "contacts": None}),
    )


async def test_the_reader_returns_scenarios_of_the_organisation_and_of_the_group(
    installation: AsyncEngine,
) -> None:
    await seed(installation)

    by_organisation, by_group = await read(installation)

    (storing,) = by_organisation[ORG]
    assert (storing.kind, storing.key) == ("organisation_scenario", ("Storing",))
    assert storing.fields == SCENARIO
    (picture,) = storing.children["image"]
    assert (picture.kind, picture.key) == ("scenario_image", ())
    assert picture.fields == IMAGE
    (eigen,) = by_group[(ORG, GROUP)]
    assert (eigen.kind, eigen.fields["contacts"], eigen.children.get("image", ())) == (
        "group_scenario",
        None,
        (),
    )


async def test_nothing_is_read_from_an_installation_without_scenarios(
    installation: AsyncEngine,
) -> None:
    assert await read(installation) == ({}, {})


@pytest.mark.parametrize("secrets", [True, False])
async def test_a_document_of_what_was_read_changes_nothing(
    installation: AsyncEngine, secrets: bool
) -> None:
    await seed(installation)
    by_organisation, by_group = await read(installation)
    group = Entity(
        "group",
        (GROUP,),
        fields={"name": "Aangifte"},
        children={"closure_scenarios": by_group[(ORG, GROUP)]},
    )
    tree = organisations(**{ORG: {"closure_scenarios": by_organisation[ORG], "groups": (group,)}})

    plan = plan_against(document_of(tree, secrets=secrets), tree)

    assert plan.refusals == ()
    assert plan.changes == ()


async def test_an_update_writes_only_the_fields_it_names(installation: AsyncEngine) -> None:
    await seed(installation)
    values = {"notices": {**NOTICES, "nl": "Anders."}, "headings": {}, "contacts": None}

    await apply(
        installation, organisation_scenario("Storing", ChangeAction.UPDATE, values, "notices")
    )

    (storing,) = (await read(installation))[0][ORG]
    assert storing.fields == {**SCENARIO, "notices": values["notices"]}
    await apply(
        installation,
        organisation_scenario("Storing", ChangeAction.UPDATE, values, "headings", "contacts"),
    )
    (storing,) = (await read(installation))[0][ORG]
    assert (storing.fields["headings"], storing.fields["contacts"]) == ({}, None)


async def test_an_image_is_replaced_without_leaving_the_old_row_and_edited_in_place(
    installation: AsyncEngine,
) -> None:
    await seed(installation)
    other = Binary("image/png", png(metadata=False) + b"")
    values = {**IMAGE, "alts": {"nl": "Anders", "en": "Other"}}

    await apply(installation, image("Storing", ChangeAction.UPDATE, values, "alts"))
    (picture,) = (await read(installation))[0][ORG][0].children["image"]
    assert picture.fields["alts"] == {"nl": "Anders", "en": "Other"}
    assert picture.fields["data"] == IMAGE["data"]
    await apply(
        installation, image("Storing", ChangeAction.UPDATE, {**values, "data": other}, "data")
    )
    async with AsyncSession(installation) as session:
        assert await session.scalar(select(func.count()).select_from(ClosureImageRow)) == 1


async def test_an_image_is_removed_and_a_scenario_delete_takes_its_image(
    installation: AsyncEngine,
) -> None:
    await seed(installation)

    await apply(installation, image("Storing", ChangeAction.DELETE, {}))
    assert "image" not in (await read(installation))[0][ORG][0].children
    await apply(installation, image("Storing", ChangeAction.CREATE, IMAGE))
    await apply(installation, organisation_scenario("Storing", ChangeAction.DELETE, {}))

    async with AsyncSession(installation) as session:
        scenarios = await session.scalar(select(func.count()).select_from(ClosureScenarioRow))
        images = await session.scalar(select(func.count()).select_from(ClosureImageRow))
    assert (scenarios, images) == (1, 0)  # the group's own scenario is left


async def test_an_image_of_a_group_scenario(installation: AsyncEngine) -> None:
    await seed(installation)

    await apply(installation, image("Eigen", ChangeAction.CREATE, IMAGE, group=GROUP))

    (eigen,) = (await read(installation))[1][(ORG, GROUP)]
    assert eigen.children["image"][0].fields == IMAGE


async def test_a_group_may_reuse_the_name_of_an_organisation_scenario(
    installation: AsyncEngine,
) -> None:
    await seed(installation)

    await apply(installation, group_scenario("Storing", ChangeAction.CREATE, SCENARIO))

    by_organisation, by_group = await read(installation)
    assert [e.key for e in by_organisation[ORG]] == [("Storing",)]
    assert [e.key for e in by_group[(ORG, GROUP)]] == [("Eigen",), ("Storing",)]


async def test_deleting_what_is_gone_is_quiet_and_updating_it_is_refused(
    installation: AsyncEngine,
) -> None:
    await apply(installation, organisation_scenario("Weg", ChangeAction.DELETE, {}))

    with pytest.raises(DomainError) as refused:
        await apply(
            installation, organisation_scenario("Weg", ChangeAction.UPDATE, SCENARIO, "notices")
        )

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION


async def test_a_second_create_an_unknown_group_and_a_missing_scenario_are_refused(
    installation: AsyncEngine,
) -> None:
    await seed(installation)
    bad = [
        organisation_scenario("Storing", ChangeAction.CREATE, SCENARIO),
        change("group_scenario", "X", ChangeAction.CREATE, SCENARIO, group="nope"),
        image("Weg", ChangeAction.CREATE, IMAGE),
        image("Weg", ChangeAction.DELETE, {}),
        image("Eigen", ChangeAction.CREATE, {**IMAGE, "data": None}, group=GROUP),
    ]

    for one in bad:
        with pytest.raises(DomainError) as refused:
            await apply(installation, one)
        assert refused.value.code in {
            ErrorCode.CONFIGURATION_OBJECT_EXISTS,
            ErrorCode.INVALID_CONFIGURATION,
        }
