"""Groups read from and written to the database for a configuration document (ADR 0099)."""

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.config_group import WRITERS
from app.adapters.db.models import (
    BookingRow,
    CapacityPoolRow,
    GroupContactsRow,
    GroupFormFieldRow,
    GroupRow,
    GroupTextRow,
    ReminderRuleRow,
    ServiceFlowRow,
    SlotRow,
)
from app.domain.configuration_changes import ChangeAction, RuleContext, Usage
from app.domain.configuration_document import Document, Mode, Scope
from app.domain.configuration_plan import plan_document
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import V1
from app.domain.errors import DomainError, ErrorCode
from tests.support.config_seed import (
    CHRISTMAS,
    CONTACTS,
    NOW,
    apply_document,
    change,
    installation,
    reread,
    seed,
)
from tests.support.configuration import allow


@pytest.fixture
async def seeded(engine: AsyncEngine) -> AsyncEngine:
    await seed(engine)
    return engine


def fields_of(groups: dict[str, tuple[Entity, ...]], slug: str) -> dict[str, object]:
    (found,) = [g for g in groups["acme"] if g.key == (slug,)]
    return dict(found.fields)


async def test_the_groups_are_read_in_the_form_a_document_carries(seeded: AsyncEngine) -> None:
    groups = await reread(seeded)

    assert [g.key for g in groups["acme"]] == [("helpdesk",), ("loket",)]
    helpdesk = fields_of(groups, "helpdesk")
    assert helpdesk["idp_key"] == "helpdesk-idp"
    assert helpdesk["archived"] is False
    assert helpdesk["holiday_source"] == "own"
    assert helpdesk["holiday_calendars"] == ["nl"]
    assert helpdesk["holiday_open_days"] == [CHRISTMAS.isoformat()]
    assert helpdesk["contacts"] == CONTACTS
    assert helpdesk["routing"]["subjects"][0]["code"] == "billing"  # type: ignore[index]
    assert helpdesk["reminder_rules"] == [
        {"channel": "email", "minutes_before": 1440, "enabled": True}
    ]
    rules = helpdesk["schedule_rules"]
    assert [r["weekday"] for r in rules] == [0, 1]  # type: ignore[index]
    assert rules[1]["subjects"] == ["billing"]  # type: ignore[index]
    assert rules[1]["valid_from"] == "2026-01-01"  # type: ignore[index]
    overrides = helpdesk["schedule_overrides"]
    assert overrides[0]["opens"] is None  # type: ignore[index]  # a closed day
    assert overrides[1]["capacity"] == 1  # type: ignore[index]
    form = helpdesk["form_fields"]
    assert [(f["key"], f["retired"]) for f in form] == [("klantnummer", False), ("oud", True)]  # type: ignore[index]
    loket = fields_of(groups, "loket")
    assert loket["idp_key"] is None
    assert loket["contacts"] is None
    assert loket["routing"] is None
    assert loket["form_fields"] == []
    (text,) = [g for g in groups["acme"] if g.key == ("helpdesk",)][0].children["texts"]
    assert (text.key, text.fields["text"]) == (("nl", "message.signoff"), "Tot ziens")


async def test_what_is_read_is_valid_by_the_rules_of_the_format(seeded: AsyncEngine) -> None:
    async with AsyncSession(seeded) as session:
        stored = await installation(session)
    # the same tree planned as a document against an installation without the groups
    bare = Entity(
        "installation",
        children={
            "platform": stored.children["platform"],
            "organisations": (Entity("organisation", ("acme",), fields={"name": "Acme"}),),
        },
    )
    document = Document(V1.name, Mode.MERGE, Scope(), stored, "digest", V1)

    created = plan_document(
        document,
        bare,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(now=NOW),
        settings_version=1,
    )
    unchanged = plan_document(
        document,
        stored,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(now=NOW),
        settings_version=1,
    )

    assert created.refusals == ()
    assert {c.kind for c in created.changes if c.action is ChangeAction.CREATE} >= {
        "group",
        "group_text",
    }
    assert unchanged.refusals == ()
    assert unchanged.changes == ()


async def test_a_group_is_created_with_everything_it_holds(engine: AsyncEngine) -> None:
    await seed(engine)
    data = {
        "organisations": [
            {
                "slug": "acme",
                "groups": [
                    {
                        "slug": "balie",
                        "name": "Balie",
                        "timezone": "Europe/Amsterdam",
                        "slot_minutes": 20,
                        "schedule_rules": [
                            {"weekday": 2, "opens": "10:00", "closes": "12:00", "capacity": 2}
                        ],
                        "schedule_overrides": [{"day": "2026-12-26"}],
                        "reminder_rules": [{"channel": "email", "minutes_before": 60}],
                        "holiday_source": "own",
                        "holiday_calendars": ["nl"],
                        "holiday_open_days": ["2026-12-25"],
                        "contacts": CONTACTS,
                        "form_fields": [
                            {
                                "key": "vraag",
                                "type": "short_text",
                                "labels": {"nl": "Vraag", "en": "Question"},
                            }
                        ],
                        "texts": {"nl": {"message.signoff": "Groet"}},
                    }
                ],
            }
        ]
    }

    plan = await apply_document(engine, data)

    assert plan.ok, plan.refusals
    balie = fields_of(await reread(engine), "balie")
    assert balie["slot_minutes"] == 20
    assert balie["idp_key"] is None
    assert balie["holiday_calendars"] == ["nl"]
    assert balie["holiday_open_days"] == ["2026-12-25"]
    assert balie["contacts"] == CONTACTS
    assert [f["key"] for f in balie["form_fields"]] == ["vraag"]  # type: ignore[index]
    assert balie["schedule_overrides"][0]["opens"] is None  # type: ignore[index]
    async with AsyncSession(engine) as session:
        texts = (await session.scalars(select(GroupTextRow.text))).all()
    assert sorted(texts) == ["Groet", "Tot ziens"]


async def test_updating_replaces_the_lists_it_names_and_leaves_the_rest(
    seeded: AsyncEngine,
) -> None:
    before = fields_of(await reread(seeded), "helpdesk")
    data = {
        "organisations": [
            {
                "slug": "acme",
                "groups": [
                    {
                        "slug": "helpdesk",
                        "name": "Servicedesk",
                        "idp_key": None,
                        "schedule_rules": [
                            {"weekday": 3, "opens": "08:00", "closes": "09:00", "capacity": 1}
                        ],
                        "holiday_open_days": [],
                        "contacts": None,
                        "routing": None,
                        "reminder_rules": [],
                    }
                ],
            }
        ]
    }

    plan = await apply_document(seeded, data)

    assert plan.ok, plan.refusals
    after = fields_of(await reread(seeded), "helpdesk")
    assert after["name"] == "Servicedesk"
    assert after["idp_key"] is None
    assert [r["weekday"] for r in after["schedule_rules"]] == [3]  # type: ignore[index]
    assert after["holiday_open_days"] == []
    assert after["contacts"] is None
    assert after["routing"] is None
    assert after["reminder_rules"] == []
    assert after["schedule_overrides"] == before["schedule_overrides"]
    assert after["holiday_calendars"] == before["holiday_calendars"]
    async with AsyncSession(seeded) as session:
        assert await session.scalar(select(func.count()).select_from(GroupContactsRow)) == 0
        assert await session.scalar(select(func.count()).select_from(ReminderRuleRow)) == 0


async def test_the_form_follows_the_list_and_retires_what_it_leaves_out(
    seeded: AsyncEngine,
) -> None:
    new_field = {"key": "nieuw", "type": "short_text", "labels": {"nl": "Nieuw", "en": "New"}}
    data = {
        "organisations": [
            {"slug": "acme", "groups": [{"slug": "helpdesk", "form_fields": [new_field]}]}
        ]
    }

    plan = await apply_document(seeded, data)

    assert plan.ok, plan.refusals
    form = fields_of(await reread(seeded), "helpdesk")["form_fields"]
    assert [(f["key"], f["retired"]) for f in form] == [  # type: ignore[index]
        ("nieuw", False),
        ("klantnummer", True),
        ("oud", True),
    ]
    async with AsyncSession(seeded) as session:
        version = await session.scalar(
            select(GroupRow.form_version).where(GroupRow.slug == "helpdesk")
        )
        positions = (await session.scalars(select(GroupFormFieldRow.position))).all()
    assert version == 2
    assert sorted(positions) == [0, 1, 2]


async def test_a_form_that_does_not_change_keeps_its_version(seeded: AsyncEngine) -> None:
    form = fields_of(await reread(seeded), "helpdesk")["form_fields"]
    data = {
        "organisations": [{"slug": "acme", "groups": [{"slug": "helpdesk", "form_fields": form}]}]
    }

    plan = await apply_document(seeded, data)

    assert plan.ok, plan.refusals
    assert plan.changes == ()
    async with AsyncSession(seeded) as session:
        version = await session.scalar(
            select(GroupRow.form_version).where(GroupRow.slug == "helpdesk")
        )
    assert version == 1


async def test_a_group_is_archived_once_and_never_the_other_way(seeded: AsyncEngine) -> None:
    data = {"organisations": [{"slug": "acme", "groups": [{"slug": "loket", "archived": True}]}]}

    plan = await apply_document(seeded, data)

    assert plan.ok, plan.refusals
    assert fields_of(await reread(seeded), "loket")["archived"] is True
    back = await apply_document(
        seeded,
        {"organisations": [{"slug": "acme", "groups": [{"slug": "loket", "archived": False}]}]},
    )
    assert [r.code for r in back.refusals] == [ErrorCode.GROUP_ARCHIVED]


async def test_a_group_and_its_slots_are_deleted(seeded: AsyncEngine) -> None:
    async with AsyncSession(seeded) as session, session.begin():
        loket = await session.scalar(select(GroupRow.id).where(GroupRow.slug == "loket"))
        session.add(SlotRow(group_id=loket, start=NOW, end=NOW, capacity_snapshot=1))
    data = {"organisations": [{"slug": "acme", "groups": [{"slug": "loket", "action": "delete"}]}]}

    plan = await apply_document(seeded, data)

    assert plan.ok, plan.refusals
    assert [g.key for g in (await reread(seeded))["acme"]] == [("helpdesk",)]
    async with AsyncSession(seeded) as session:
        assert await session.scalar(select(func.count()).select_from(SlotRow)) == 0


async def flows_by_group(engine: AsyncEngine) -> dict[str, list[tuple[str, str]]]:
    """Return each group's flows as ``(code, state)`` and the number of pools, by slug."""
    async with AsyncSession(engine) as session:
        rows = (
            await session.execute(
                select(GroupRow.slug, ServiceFlowRow.code, ServiceFlowRow.state)
                .join(ServiceFlowRow, ServiceFlowRow.group_id == GroupRow.id)
                .order_by(GroupRow.slug, ServiceFlowRow.id)
            )
        ).all()
    found: dict[str, list[tuple[str, str]]] = {}
    for slug, code, state in rows:
        found.setdefault(slug, []).append((code, state))
    return found


async def count_of(engine: AsyncEngine, table: type) -> int:
    """Return the number of rows of ``table``."""
    async with AsyncSession(engine) as session:
        return int(await session.scalar(select(func.count()).select_from(table)) or 0)


async def test_an_imported_group_gets_its_pool_and_flows_and_loses_them_when_deleted(
    engine: AsyncEngine,
) -> None:
    await seed(engine)
    rules = [
        {"weekday": 2, "opens": "10:00", "closes": "12:00", "capacity": 2, "offer": "both"},
    ]
    data = {
        "organisations": [
            {
                "slug": "acme",
                "groups": [
                    {
                        "slug": "balie",
                        "name": "Balie",
                        "timezone": "Europe/Amsterdam",
                        "slot_minutes": 30,
                    },
                    {
                        "slug": "direct",
                        "name": "Direct",
                        "timezone": "Europe/Amsterdam",
                        "slot_minutes": 30,
                        "schedule_rules": rules,
                    },
                ],
            }
        ]
    }

    plan = await apply_document(engine, data, whole_apply=True)

    assert plan.ok, plan.refusals
    found = await flows_by_group(engine)
    assert found["balie"] == [("appointment", "active")]
    assert found["direct"] == [("appointment", "active"), ("direct", "active")]
    pools_before = await count_of(engine, CapacityPoolRow)
    gone = {
        "organisations": [
            {
                "slug": "acme",
                "groups": [
                    {"slug": "balie", "action": "delete"},
                    {"slug": "direct", "action": "delete"},
                ],
            }
        ]
    }
    assert (await apply_document(engine, gone, whole_apply=True)).ok
    assert await count_of(engine, CapacityPoolRow) == pools_before - 2
    assert "balie" not in await flows_by_group(engine)


async def test_a_change_of_the_rules_gives_an_existing_group_the_direct_flow(
    seeded: AsyncEngine,
) -> None:
    assert await flows_by_group(seeded) == {}
    rules = [{"weekday": 3, "opens": "08:00", "closes": "09:00", "capacity": 1, "offer": "direct"}]
    data = {
        "organisations": [{"slug": "acme", "groups": [{"slug": "loket", "schedule_rules": rules}]}]
    }

    assert (await apply_document(seeded, data, whole_apply=True)).ok

    assert await flows_by_group(seeded) == {
        "loket": [("appointment", "active"), ("direct", "active")]
    }


async def test_a_change_that_leaves_the_rules_alone_writes_no_flows(seeded: AsyncEngine) -> None:
    data = {"organisations": [{"slug": "acme", "groups": [{"slug": "loket", "name": "Nieuw"}]}]}

    assert (await apply_document(seeded, data, whole_apply=True)).ok

    assert await flows_by_group(seeded) == {}


async def test_a_text_is_set_changed_and_removed(seeded: AsyncEngine) -> None:
    def doc(texts: dict[str, object]) -> dict[str, object]:
        return {
            "organisations": [{"slug": "acme", "groups": [{"slug": "helpdesk", "texts": texts}]}]
        }

    await apply_document(
        seeded, doc({"nl": {"message.signoff": "Groet", "message.reason.staffing": "Drukte"}})
    )
    async with AsyncSession(seeded) as session:
        stored = dict((await session.execute(select(GroupTextRow.key, GroupTextRow.text))).all())
    assert stored == {"message.signoff": "Groet", "message.reason.staffing": "Drukte"}

    await apply_document(seeded, doc({"nl": {"message.signoff": None}}))
    async with AsyncSession(seeded) as session:
        stored = dict((await session.execute(select(GroupTextRow.key, GroupTextRow.text))).all())
    assert stored == {"message.reason.staffing": "Drukte"}


async def test_a_writer_refuses_what_does_not_resolve(seeded: AsyncEngine) -> None:
    values = {"name": "X", "timezone": "UTC", "slot_minutes": 30}
    async with AsyncSession(seeded) as session, session.begin():
        with pytest.raises(DomainError) as organisation:
            await WRITERS["group"](
                session, None, change("group", ("nope", "x"), ChangeAction.CREATE, values)
            )
        with pytest.raises(DomainError) as group:
            await WRITERS["group"](
                session, None, change("group", ("acme", "nope"), ChangeAction.UPDATE, {"name": "Y"})
            )
        with pytest.raises(DomainError) as calendar:
            await WRITERS["group"](
                session,
                None,
                change(
                    "group", ("acme", "loket"), ChangeAction.UPDATE, {"holiday_calendars": ["zz"]}
                ),
            )
        with pytest.raises(DomainError) as text:
            await WRITERS["group_text"](
                session,
                None,
                change(
                    "group_text", ("acme", "nope", "nl", "k"), ChangeAction.UPDATE, {"text": "t"}
                ),
            )
    assert organisation.value.code is ErrorCode.ORGANISATION_NOT_FOUND
    assert group.value.code is ErrorCode.GROUP_NOT_FOUND
    assert calendar.value.code is ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND
    assert text.value.code is ErrorCode.GROUP_NOT_FOUND


async def test_a_retired_field_that_becomes_personal_data_raises_the_stored_answers(
    seeded: AsyncEngine,
) -> None:
    async with AsyncSession(seeded) as session, session.begin():
        group = await session.scalar(select(GroupRow).where(GroupRow.slug == "helpdesk"))
        assert group is not None
        row = await session.scalar(
            select(GroupFormFieldRow).where(GroupFormFieldRow.key == "klantnummer")
        )
        assert row is not None
        row.classification = "not_pii"
        slot = SlotRow(group_id=group.id, start=NOW, end=NOW, capacity_snapshot=1)
        session.add(slot)
        await session.flush()
        session.add(
            BookingRow(
                slot_id=slot.id,
                group_id=group.id,
                status="confirmed",
                locale="nl",
                management_token_hash="h" * 64,
                public_ref="ref-1",
                created_at=NOW,
                form_answers=json.dumps(
                    {"answers": {"klantnummer": "7"}, "not_pii": ["klantnummer"]}
                ),
            )
        )
    data = {
        "organisations": [{"slug": "acme", "groups": [{"slug": "helpdesk", "form_fields": []}]}]
    }

    plan = await apply_document(seeded, data)

    assert plan.ok, plan.refusals
    async with AsyncSession(seeded) as session:
        stored = json.loads(str(await session.scalar(select(BookingRow.form_answers))))
    assert "klantnummer" not in stored.get("not_pii", [])


def test_the_seed_dates_are_after_the_fixed_time() -> None:
    assert NOW.date() < CHRISTMAS
