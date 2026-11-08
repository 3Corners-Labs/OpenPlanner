"""Alert targets of a configuration document on SQLite (ADR 0099 step 2)."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.config_alerts import WRITERS, read_alert_targets
from app.adapters.db.models import AlertDeliveryRow, AlertTargetRow, OrganisationRow
from app.domain.configuration_changes import Change, ChangeAction, RuleContext, Usage
from app.domain.configuration_document import Document, Mode, Scope, parse_document, to_json
from app.domain.configuration_plan import plan_document
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import FORMATS, V1
from app.domain.errors import DomainError, ErrorCode
from tests.support.configuration import allow

PLATFORM = "platform_alert_target"
ORGANISATION = "organisation_alert_target"


def change(
    kind: str,
    key: tuple[str, str],
    action: ChangeAction,
    level: str | None = None,
    owner: str | None = None,
) -> Change:
    values = {} if level is None else {"min_level": level}
    return Change(
        kind=kind,
        key=key,
        address=(owner, *key) if owner else key,  # type: ignore[arg-type]
        path="",
        action=action,
        fields=tuple(values),
        values=values,
    )


async def apply(engine: AsyncEngine, *changes: Change) -> None:
    async with AsyncSession(engine) as session, session.begin():
        for item in changes:
            await WRITERS[item.kind](session, None, item)


async def read(engine: AsyncEngine) -> dict[str | None, tuple[Entity, ...]]:
    async with AsyncSession(engine) as session:
        return await read_alert_targets(session)


async def organisation(engine: AsyncEngine, slug: str = "acme") -> None:
    async with AsyncSession(engine) as session, session.begin():
        session.add(OrganisationRow(name=slug.title(), slug=slug))


EMAIL = ("email", "ops@voorbeeld.nl")
ROOM = ("webex_space", "Y2lzY29zcGFyazovL3VzL1JPT00vMTIz")


async def test_targets_are_read_per_owner(engine: AsyncEngine) -> None:
    await organisation(engine)
    await apply(
        engine,
        change(PLATFORM, EMAIL, ChangeAction.CREATE, "warning"),
        change(ORGANISATION, ROOM, ChangeAction.CREATE, "outage", "acme"),
        change(ORGANISATION, EMAIL, ChangeAction.CREATE, "warning", "acme"),
    )

    found = await read(engine)

    assert [(e.kind, e.key, e.fields) for e in found[None]] == [
        (PLATFORM, EMAIL, {"min_level": "warning"})
    ]
    assert [(e.kind, e.key, e.fields["min_level"]) for e in found["acme"]] == [
        (ORGANISATION, EMAIL, "warning"),
        (ORGANISATION, ROOM, "outage"),
    ]


async def test_no_targets_read_as_nothing(engine: AsyncEngine) -> None:
    assert await read(engine) == {}


async def test_a_level_is_changed_and_a_same_address_elsewhere_is_another_target(
    engine: AsyncEngine,
) -> None:
    await organisation(engine)
    await apply(
        engine,
        change(PLATFORM, EMAIL, ChangeAction.CREATE, "warning"),
        change(ORGANISATION, EMAIL, ChangeAction.CREATE, "warning", "acme"),
    )

    await apply(engine, change(ORGANISATION, EMAIL, ChangeAction.UPDATE, "outage", "acme"))

    found = await read(engine)
    assert found[None][0].fields["min_level"] == "warning"
    assert found["acme"][0].fields["min_level"] == "outage"


async def test_deleting_a_target_takes_its_deliveries_and_a_missing_one_is_nothing(
    engine: AsyncEngine,
) -> None:
    await apply(engine, change(PLATFORM, EMAIL, ChangeAction.CREATE, "warning"))
    async with AsyncSession(engine) as session, session.begin():
        target = await session.scalar(select(AlertTargetRow))
        assert target is not None
        session.add(AlertDeliveryRow(target_id=target.id, alert_key="a", token="t"))

    await apply(engine, change(PLATFORM, EMAIL, ChangeAction.DELETE))
    await apply(engine, change(PLATFORM, EMAIL, ChangeAction.DELETE))

    async with AsyncSession(engine) as session:
        assert (await session.scalar(select(AlertDeliveryRow))) is None
        assert (await session.scalar(select(AlertTargetRow))) is None


async def test_an_organisation_that_is_not_there_is_refused(engine: AsyncEngine) -> None:
    with pytest.raises(DomainError) as refused:
        await apply(engine, change(ORGANISATION, EMAIL, ChangeAction.CREATE, "warning", "nobody"))

    assert refused.value.code is ErrorCode.ORGANISATION_NOT_FOUND


async def test_an_update_that_names_no_level_leaves_the_stored_one(engine: AsyncEngine) -> None:
    await apply(engine, change(PLATFORM, EMAIL, ChangeAction.CREATE, "outage"))
    untouched = Change(
        kind=PLATFORM,
        key=EMAIL,
        address=EMAIL,
        path="",
        action=ChangeAction.UPDATE,
        fields=(),
        values={"min_level": "warning"},
    )

    await apply(engine, untouched)

    assert (await read(engine))[None][0].fields["min_level"] == "outage"


def tree(found: dict[str | None, tuple[Entity, ...]]) -> Entity:
    platform = Entity("platform", (), children={"alert_targets": found.get(None, ())})
    organisations = tuple(
        Entity("organisation", (slug,), children={"alert_targets": targets})
        for slug, targets in found.items()
        if slug is not None
    )
    return Entity(
        "installation", (), children={"platform": (platform,), "organisations": organisations}
    )


async def test_the_read_targets_form_a_document_that_plans_to_no_change(
    engine: AsyncEngine,
) -> None:
    await organisation(engine)
    await apply(
        engine,
        change(PLATFORM, EMAIL, ChangeAction.CREATE, "warning"),
        change(ORGANISATION, ROOM, ChangeAction.CREATE, "outage", "acme"),
    )
    current = tree(await read(engine))
    document = Document("openplanner.config/v1", Mode.MERGE, Scope(), current, "digest", V1)

    result = plan_document(
        document,
        current,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(production=False),
        settings_version=1,
    )

    assert (result.refusals, result.changes) == ((), ())
    written = to_json(document)
    assert written["platform"] == {"alert_targets": {"email": {"ops@voorbeeld.nl": "warning"}}}
    reread = parse_document(json.loads(json.dumps(written)), FORMATS)
    again = plan_document(
        reread,
        current,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(production=False),
        settings_version=1,
    )
    assert (again.refusals, again.changes) == ((), ())
