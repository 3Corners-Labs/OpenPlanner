"""Skills, pools and flows in a configuration document on SQLite (ADR 0121 step 2b-ii, A29)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.configuration_json import JsonCodec
from app.adapters.configuration_seal import PassphraseSealing
from app.adapters.crypto import SecretBox
from app.adapters.db.config_flows import read_catalogue, write_flow_revision
from app.adapters.db.config_usage import read_usage
from app.adapters.db.engine import create_engine
from app.adapters.db.models import (
    BookingRow,
    CapacityPoolRow,
    CapacityPoolSkillRow,
    FlowRevisionRow,
    FlowRevisionSkillRow,
    GroupRow,
    OrganisationRow,
    ServiceFlowRow,
    SkillRow,
    SlotRow,
    StaffRow,
    StaffSkillRow,
    SubjectFlowRow,
    SubjectFlowSkillRow,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.configuration_changes import ChangeAction, RuleContext
from app.domain.configuration_document import Scope
from app.domain.configuration_document_service import Actor, ConfigurationDocumentService
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.support.config_changes import planned
from tests.support.config_seed import seed
from tests.support.database import migrated_database

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
HOST = Actor.host()
PASSPHRASE = "a long enough passphrase"
LABELS = json.dumps({"nl": "Balie", "en": "Desk"})
STAFF_NAME, STAFF_MAIL = "Jan Jansen", "jan@voorbeeld.nl"


def service(engine: AsyncEngine, key: bytes = b"a") -> ConfigurationDocumentService:
    clock = FixedClock(NOW)
    uow = SqlUnitOfWork(engine, SecretBox(key * 32))
    return ConfigurationDocumentService(
        uow,
        clock,
        AdminService(uow, clock),
        JsonCodec(),
        PassphraseSealing(),
        installation="test",
        app_version="0.0.0",
        context=lambda: RuleContext(),
        locked=lambda: frozenset(),
        max_bytes=lambda: 8 * 1024 * 1024,
        max_changes=lambda: 500,
        plaintext_export=lambda: True,
    )


async def export(engine: AsyncEngine) -> dict[str, Any]:
    return json.loads(await service(engine).export(HOST, Scope(), None, secrets=False))  # type: ignore[no-any-return]


async def transfer(source: AsyncEngine, target: AsyncEngine) -> Any:
    """Export the source with its secrets sealed and apply it to the target (another key)."""
    sealed = await service(source).export(HOST, Scope(), PASSPHRASE, secrets=True)
    return await service(target, b"b").apply(HOST, sealed, PASSPHRASE, None)


def encode(data: dict[str, Any]) -> bytes:
    return json.dumps(data).encode()


async def seed_flows(engine: AsyncEngine) -> None:
    """Two organisations, three services, and everything ADR 0121 step 2b stores for them."""
    await seed(engine)
    async with AsyncSession(engine) as session, session.begin():
        acme = await session.scalar(select(OrganisationRow).where(OrganisationRow.slug == "acme"))
        assert acme is not None
        helpdesk = await session.scalar(select(GroupRow).where(GroupRow.slug == "helpdesk"))
        loket = await session.scalar(select(GroupRow).where(GroupRow.slug == "loket"))
        assert helpdesk is not None
        assert loket is not None
        beta = OrganisationRow(name="Beta", slug="beta")
        session.add(beta)
        await session.flush()
        vestiging = GroupRow(
            organisation_id=beta.id,
            name="Vestiging",
            slug="vestiging",
            timezone="Europe/Amsterdam",
            slot_minutes=30,
            min_notice_minutes=60,
            horizon_days=60,
            phone_mode="optional",
        )
        staff = StaffRow(
            subject="jan",
            display_name=STAFF_NAME,
            email=STAFF_MAIL,
            first_seen_at=NOW,
            last_login_at=NOW,
            last_seen_at=NOW,
        )
        passport = SkillRow(organisation_id=acme.id, code="passport", labels=LABELS)
        old = SkillRow(organisation_id=acme.id, code="old", labels=LABELS, archived_at=NOW)
        id_card = SkillRow(organisation_id=beta.id, code="id-card", labels=LABELS)
        shared = CapacityPoolRow(organisation_id=acme.id, name="Kantoor", mode="counted")
        session.add_all([vestiging, staff, passport, old, id_card, shared])
        await session.flush()
        pools = {
            group.slug: CapacityPoolRow(
                organisation_id=group.organisation_id,
                name=group.name,
                mode="legacy_group",
                group_id=group.id,
            )
            for group in (helpdesk, loket, vestiging)
        }
        session.add_all(pools.values())
        session.add_all(
            [
                CapacityPoolSkillRow(pool_id=shared.id, skill_id=passport.id),
                StaffSkillRow(staff_id=staff.id, skill_id=passport.id, granted_at=NOW),
            ]
        )
        appointment = ServiceFlowRow(
            group_id=helpdesk.id,
            code="appointment",
            state="active",
            default_appointment=True,
            current_revision=1,
        )
        advies = ServiceFlowRow(
            group_id=helpdesk.id,
            code="advies",
            state="archived",
            current_revision=2,
            draft_revision=3,
        )
        telefoon = ServiceFlowRow(
            group_id=loket.id, code="telefoon", state="draft", current_revision=1
        )
        session.add_all([appointment, advies, telefoon])
        await session.flush()
        revisions = [
            FlowRevisionRow(
                flow_id=appointment.id,
                revision=1,
                labels=LABELS,
                intake_modes="appointment",
                interaction_kind="video",
                duration_minutes=30,
                capacity_pool_id=pools["helpdesk"].id,
                dispatch="own_queue",
                handling_action="open_video",
                completion="legacy_video_observation",
                legacy=True,
                published_at=NOW,
            )
        ]
        for number, published in ((1, True), (2, True), (3, False)):
            revisions.append(
                FlowRevisionRow(
                    flow_id=advies.id,
                    revision=number,
                    labels=LABELS,
                    intake_modes="appointment",
                    interaction_kind="telephone",
                    duration_minutes=20,
                    capacity_pool_id=shared.id,
                    dispatch="own_queue",
                    handling_action="call",
                    completion="operator",
                    published_at=NOW if published else None,
                    published_by=staff.id if published else None,
                )
            )
        revisions.append(
            FlowRevisionRow(
                flow_id=telefoon.id,
                revision=1,
                labels=LABELS,
                intake_modes="appointment,on_demand",
                interaction_kind="telephone",
                duration_minutes=15,
                capacity_pool_id=shared.id,
                dispatch="own_queue",
                handling_action="call",
                completion="operator",
                published_at=NOW,
            )
        )
        session.add_all(revisions)
        await session.flush()
        session.add_all(
            [
                FlowRevisionSkillRow(flow_id=advies.id, revision=2, skill_id=passport.id),
                SubjectFlowRow(flow_id=advies.id, revision=2, subject_code="billing"),
                SubjectFlowSkillRow(
                    flow_id=advies.id, revision=2, subject_code="billing", skill_id=passport.id
                ),
                SubjectFlowRow(flow_id=telefoon.id, revision=1, subject_code="billing"),
            ]
        )


@pytest.fixture
async def source(database_url: str) -> AsyncEngine:
    engine = create_engine(database_url, busy_timeout_ms=200)
    await seed_flows(engine)
    yield engine  # type: ignore[misc]
    await engine.dispose()


@pytest.fixture
async def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncEngine:
    engine = create_engine(migrated_database(tmp_path, monkeypatch, "target"), busy_timeout_ms=200)
    yield engine  # type: ignore[misc]
    await engine.dispose()


async def count(engine: AsyncEngine, table: type) -> int:
    async with AsyncSession(engine) as session:
        return len((await session.scalars(select(table))).all())


# --- the round trip (A29) ---------------------------------------------------------------


async def test_export_import_export_gives_the_same_document(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    exported = await export(source)
    applied = await transfer(source, target)

    assert applied.plan.refusals == (), applied.plan.refusals
    assert applied.applied
    again = json.loads(await service(target, b"b").export(HOST, Scope(), None, secrets=False))
    assert again == exported
    assert {"skills", "capacity_pools"} <= set(exported["organisations"][0])
    group = exported["organisations"][0]["groups"][0]
    assert {"legacy_pool", "flows"} <= set(group)


async def test_the_document_holds_the_history_of_a_flow(source: AsyncEngine) -> None:
    exported = await export(source)

    helpdesk = next(g for g in exported["organisations"][0]["groups"] if g["slug"] == "helpdesk")
    advies = next(f for f in helpdesk["flows"] if f["code"] == "advies")
    assert [r["revision"] for r in advies["revisions"]] == [1, 2, 3]
    assert (advies["current_revision"], advies["draft_revision"]) == (2, 3)
    assert advies["revisions"][2]["published_at"] is None
    assert advies["revisions"][1]["subjects"] == {"billing": ["passport"]}
    assert advies["revisions"][1]["capacity_pool"] == "Kantoor"
    legacy = next(f for f in helpdesk["flows"] if f["code"] == "appointment")["revisions"][0]
    assert (legacy["legacy"], legacy["legacy_pool_of"], legacy["capacity_pool"]) == (
        True,
        "helpdesk",
        None,
    )


async def test_importing_into_the_same_installation_plans_nothing(source: AsyncEngine) -> None:
    exported = await export(source)

    plan = await service(source).plan(HOST, encode(exported), None)

    assert plan.refusals == ()
    assert plan.changes == (), plan.lines()


async def test_applying_twice_changes_nothing_the_second_time(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    sealed = await service(source).export(HOST, Scope(), PASSPHRASE, secrets=True)
    await service(target, b"b").apply(HOST, sealed, PASSPHRASE, None)

    second = await service(target, b"b").plan(HOST, sealed, PASSPHRASE)

    assert second.changes == (), second.lines()


async def test_the_database_ids_differ_but_every_reference_still_resolves(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    await transfer(source, target)

    async with AsyncSession(target) as session:
        revision = await session.scalar(
            select(FlowRevisionRow)
            .join(ServiceFlowRow)
            .where(ServiceFlowRow.code == "advies", FlowRevisionRow.revision == 2)
        )
        assert revision is not None
        pool = await session.get(CapacityPoolRow, revision.capacity_pool_id)
        assert pool is not None
        assert (pool.name, pool.mode) == ("Kantoor", "counted")
        skills = list(
            await session.scalars(
                select(SkillRow.code)
                .join(FlowRevisionSkillRow, FlowRevisionSkillRow.skill_id == SkillRow.id)
                .where(FlowRevisionSkillRow.flow_id == revision.flow_id)
            )
        )
        assert skills == ["passport"]
        draft = await session.get(FlowRevisionRow, (revision.flow_id, 3))
        assert draft is not None
        assert draft.published_at is None
        assert revision.published_by is None
    assert await count(target, StaffSkillRow) == 0  # who holds a skill is state


# --- staff and grants are state ---------------------------------------------------------


async def test_no_staff_identity_and_no_grant_is_in_the_document(source: AsyncEngine) -> None:
    exported = await service(source).export(HOST, Scope(), None, secrets=False)

    assert STAFF_NAME.encode() not in exported
    assert STAFF_MAIL.encode() not in exported
    assert b"published_by" not in exported
    assert b"staff" not in exported
    assert b"granted" not in exported


# --- documents of the previous version --------------------------------------------------


async def test_a_document_without_the_sections_leaves_what_the_installation_has(
    source: AsyncEngine,
) -> None:
    exported = await export(source)
    for organisation in exported["organisations"]:
        organisation.pop("skills", None)
        organisation.pop("capacity_pools", None)
        for group in organisation["groups"]:
            group.pop("legacy_pool", None)
            group.pop("flows", None)
    exported["mode"] = "replace"

    result = await service(source).apply(HOST, encode(exported), None, None)

    assert result.plan.refusals == ()
    assert result.plan.changes == ()
    assert await count(source, SkillRow) == 3
    assert await count(source, ServiceFlowRow) == 3
    assert await count(source, CapacityPoolRow) == 4


# --- replace: archive instead of delete -------------------------------------------------


async def book_appointment(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session, session.begin():
        helpdesk = await session.scalar(select(GroupRow).where(GroupRow.slug == "helpdesk"))
        flow = await session.scalar(
            select(ServiceFlowRow.id).where(ServiceFlowRow.code == "appointment")
        )
        assert helpdesk is not None
        slot = SlotRow(group_id=helpdesk.id, start=NOW, end=NOW, capacity_snapshot=1)
        session.add(slot)
        await session.flush()
        session.add(
            BookingRow(
                slot_id=slot.id,
                group_id=helpdesk.id,
                status="cancelled",
                locale="nl",
                management_token_hash="a" * 64,
                public_ref="ref-a",
                created_at=NOW,
                flow_id=flow,
                flow_revision=1,
            )
        )


async def test_what_a_replace_document_leaves_out_is_archived_when_it_is_in_use(
    source: AsyncEngine,
) -> None:
    await book_appointment(source)
    exported = await export(source)
    exported["mode"] = "replace"
    acme = exported["organisations"][0]
    acme["skills"] = []
    acme["capacity_pools"] = [p for p in acme["capacity_pools"] if p["name"] != "Kantoor"]
    for group in acme["groups"]:
        group["flows"] = [f for f in group.get("flows", []) if f["code"] == "advies"]

    plan = await service(source).plan(HOST, encode(exported), None)

    lines = plan.lines()
    assert plan.refusals == ()
    assert "archive service_flow acme/helpdesk/appointment: state" in lines  # booked
    assert "delete service_flow acme/loket/telefoon" in lines  # nothing refers to it
    assert "archive skill acme/passport: archived" in lines  # a person holds it
    assert "delete skill acme/old" in lines  # archived and unused
    assert "archive capacity_pool acme/Kantoor: archived" in lines

    result = await service(source).apply(HOST, encode(exported), None, plan.digest)

    assert result.applied
    async with AsyncSession(source) as session:
        appointment = await session.scalar(
            select(ServiceFlowRow).where(ServiceFlowRow.code == "appointment")
        )
        assert appointment is not None
        assert appointment.state == "archived"
        assert await session.get(FlowRevisionRow, (appointment.id, 1)) is not None
        assert (
            await session.scalar(select(ServiceFlowRow).where(ServiceFlowRow.code == "telefoon"))
            is None
        )
        passport = await session.scalar(select(SkillRow).where(SkillRow.code == "passport"))
        assert passport is not None
        assert passport.archived_at is not None
        assert await session.scalar(select(SkillRow).where(SkillRow.code == "old")) is None
        kantoor = await session.scalar(
            select(CapacityPoolRow).where(CapacityPoolRow.name == "Kantoor")
        )
        assert kantoor is not None
        assert kantoor.archived_at is not None
        assert (await session.scalars(select(StaffSkillRow))).all()  # the grant is untouched


# --- history is immutable ---------------------------------------------------------------


async def test_a_published_revision_cannot_be_changed_by_a_document(source: AsyncEngine) -> None:
    exported = await export(source)
    helpdesk = exported["organisations"][0]["groups"][0]
    advies = next(f for f in helpdesk["flows"] if f["code"] == "advies")
    advies["revisions"][0]["duration_minutes"] = 45

    plan = await service(source).plan(HOST, encode(exported), None)

    assert [(r.path, r.code, r.field) for r in plan.refusals] == [
        (
            "organisations[acme].groups[helpdesk].flows[advies].revisions[1]",
            ErrorCode.CONFIGURATION_REVISION_PUBLISHED,
            "duration_minutes",
        )
    ]


async def test_the_writer_refuses_a_published_revision_a_second_time(source: AsyncEngine) -> None:
    change = planned(
        "flow_revision",
        ("acme", "helpdesk", "advies", "2"),
        ChangeAction.UPDATE,
        {"labels": {"nl": "X", "en": "X"}},
        key=("2",),
        organisation="acme",
        group="helpdesk",
    )

    async with AsyncSession(source) as session, session.begin():
        with pytest.raises(DomainError) as refused:
            await write_flow_revision(session, None, change)

    assert refused.value.code is ErrorCode.CONFIGURATION_REVISION_PUBLISHED


async def test_a_draft_is_replaced_and_a_new_revision_added_without_renumbering(
    source: AsyncEngine,
) -> None:
    exported = await export(source)
    helpdesk = exported["organisations"][0]["groups"][0]
    advies = next(f for f in helpdesk["flows"] if f["code"] == "advies")
    fresh = {**advies["revisions"][2], "revision": 4, "labels": {"nl": "Vier", "en": "Four"}}
    advies["revisions"] = advies["revisions"][:2] + [fresh]
    advies["draft_revision"] = 4

    result = await service(source).apply(HOST, encode(exported), None, None)

    assert result.applied
    async with AsyncSession(source) as session:
        flow = await session.scalar(select(ServiceFlowRow).where(ServiceFlowRow.code == "advies"))
        assert flow is not None
        numbers = sorted(
            await session.scalars(
                select(FlowRevisionRow.revision).where(FlowRevisionRow.flow_id == flow.id)
            )
        )
        assert numbers == [1, 2, 4]  # the replaced draft 3 is gone, 1 and 2 are untouched
        assert (flow.current_revision, flow.draft_revision) == (2, 4)


# --- references and counts --------------------------------------------------------------


async def test_a_flow_cannot_use_another_organisations_pool_through_a_document(
    source: AsyncEngine,
) -> None:
    exported = await export(source)
    helpdesk = exported["organisations"][0]["groups"][0]
    advies = next(f for f in helpdesk["flows"] if f["code"] == "advies")
    advies["revisions"][2]["capacity_pool"] = None
    advies["revisions"][2]["legacy_pool_of"] = "vestiging"

    plan = await service(source).plan(HOST, encode(exported), None)

    assert [(r.code, r.field) for r in plan.refusals] == [
        (ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "capacity_pool_id")
    ]


async def test_two_pools_with_one_name_stop_an_export_instead_of_losing_one(
    source: AsyncEngine,
) -> None:
    async with AsyncSession(source) as session, session.begin():
        acme = await session.scalar(select(OrganisationRow).where(OrganisationRow.slug == "acme"))
        assert acme is not None
        session.add(CapacityPoolRow(organisation_id=acme.id, name="Kantoor", mode="counted"))

    async with AsyncSession(source) as session:
        with pytest.raises(DomainError) as refused:
            await read_catalogue(session)

    assert refused.value.code is ErrorCode.CONFIGURATION_OBJECT_EXISTS


async def test_the_usage_facts_name_what_a_deletion_would_break(source: AsyncEngine) -> None:
    await book_appointment(source)
    async with AsyncSession(source) as session:
        usage = await read_usage(session, NOW, frozenset())

    assert usage.in_use["skill"] == frozenset({("acme", "passport")})
    assert usage.in_use["capacity_pool"] == frozenset({("acme", "Kantoor")})
    assert usage.in_use["service_flow"] == frozenset({("acme", "helpdesk", "appointment")})


async def test_subject_rows_without_skills_round_trip(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    await transfer(source, target)

    async with AsyncSession(target) as session:
        subjects = list(await session.scalars(select(SubjectFlowRow.subject_code)))
        extra = list(await session.scalars(select(SubjectFlowSkillRow.subject_code)))
    assert sorted(subjects) == ["billing", "billing"]
    assert extra == ["billing"]


# --- step 2e -----------------------------------------------------------------------------


@pytest.mark.skip(reason="needs step 2e: Transaction.ensure_default_flows is not on main yet")
async def test_a_group_created_by_a_document_without_flows_gets_the_default_set(
    target: AsyncEngine,
) -> None:
    """A document of an older version creates a group; 2e's rule adds its legacy pool and flows.

    The seam is the comment at the end of ``apply_configuration`` in
    ``app/adapters/db/configuration_document.py``; once 2e lands, this test applies
    ``{"organisations": [{"slug": "x", "name": "X", "groups": [{"slug": "y", ...}]}]}`` and expects
    a legacy pool and the ``appointment`` flow, and, for a document with its own flows, exactly
    those plus anything still missing.
    """
    raise AssertionError  # pragma: no cover
