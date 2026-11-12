"""Skills, pools and flows in a configuration document on SQLite (ADR 0121 step 2b-ii, A29)."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.configuration_json import JsonCodec
from app.adapters.configuration_seal import PassphraseSealing
from app.adapters.crypto import SecretBox
from app.adapters.db.config_flows import order_flow_changes, write_flow_revision
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
from tests.support.config_seed import give_default_flows, seed
from tests.support.database import migrated_database
from tests.support.flow_documents import (
    encode,
    flow_json,
    group_json,
    organisation_document,
    revision_json,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
CLOCK = NOW + timedelta(days=1)
"""What the service takes for now: after every revision that the seed publishes."""
HOST = Actor.host()
PASSPHRASE = "a long enough passphrase"
LABELS = json.dumps({"nl": "Balie", "en": "Desk"})
STAFF_NAME, STAFF_MAIL = "Jan Jansen", "jan@voorbeeld.nl"


def service(engine: AsyncEngine, key: bytes = b"a") -> ConfigurationDocumentService:
    clock = FixedClock(CLOCK)
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
    exported: dict[str, Any] = json.loads(
        await service(engine).export(HOST, Scope(), None, secrets=False)
    )
    return exported


async def transfer(source: AsyncEngine, target: AsyncEngine) -> Any:
    """Export the source with its secrets sealed and apply it to the target (another key)."""
    sealed = await service(source).export(HOST, Scope(), PASSPHRASE, secrets=True)
    return await service(target, b"b").apply(HOST, sealed, PASSPHRASE, None)


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
        shared = CapacityPoolRow(
            organisation_id=acme.id, name="Kantoor", mode="counted", code="kantoor"
        )
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
                    published_at=NOW + timedelta(minutes=number) if published else None,
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
    await give_default_flows(engine, NOW)  # what step 2e gives a service, as for any


@pytest.fixture
async def source(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url, busy_timeout_ms=200)
    await seed_flows(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database(tmp_path, monkeypatch, "target"), busy_timeout_ms=200)
    yield engine
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
    assert advies["revisions"][1]["capacity_pool"] == "kantoor"
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
        assert (pool.name, pool.code, pool.mode) == ("Kantoor", "kantoor", "counted")
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


# --- restoring: an export of valid stored data imports into an empty installation ------------


async def archive_skill_directly(engine: AsyncEngine, code: str) -> None:
    """Archive a skill behind the port, as time passes: the revisions that require it stay."""
    async with AsyncSession(engine) as session, session.begin():
        row = await session.scalar(select(SkillRow).where(SkillRow.code == code))
        assert row is not None
        row.archived_at = NOW


async def archive_pool_directly(engine: AsyncEngine, code: str) -> None:
    """Set the pool row archived: the portal refuses archiving a pool that flows draw on."""
    async with AsyncSession(engine) as session, session.begin():
        row = await session.scalar(select(CapacityPoolRow).where(CapacityPoolRow.code == code))
        assert row is not None
        row.archived_at = NOW


async def remove_subjects(engine: AsyncEngine) -> None:
    """Let every service stop offering its subjects; the revisions that name them stay."""
    async with AsyncSession(engine) as session, session.begin():
        for group in await session.scalars(select(GroupRow)):
            group.routing = json.dumps({"subjects": []})


async def draft_of_advies(engine: AsyncEngine) -> tuple[int, int]:
    """Return the id of the flow ``advies`` and the id of the organisation ``acme``."""
    async with AsyncSession(engine) as session:
        flow = await session.scalar(select(ServiceFlowRow).where(ServiceFlowRow.code == "advies"))
        acme = await session.scalar(select(OrganisationRow).where(OrganisationRow.slug == "acme"))
        assert flow is not None
        assert acme is not None
        return flow.id, acme.id


async def draft_requires_archived_skill(engine: AsyncEngine) -> None:
    flow_id, _ = await draft_of_advies(engine)
    async with AsyncSession(engine) as session, session.begin():
        old = await session.scalar(select(SkillRow).where(SkillRow.code == "old"))
        assert old is not None
        session.add(FlowRevisionSkillRow(flow_id=flow_id, revision=3, skill_id=old.id))


async def draft_draws_on_archived_pool(engine: AsyncEngine) -> None:
    flow_id, acme = await draft_of_advies(engine)
    async with AsyncSession(engine) as session, session.begin():
        pool = CapacityPoolRow(
            organisation_id=acme, name="Oud", mode="counted", code="oud", archived_at=NOW
        )
        session.add(pool)
        await session.flush()
        draft = await session.get(FlowRevisionRow, (flow_id, 3))
        assert draft is not None
        draft.capacity_pool_id = pool.id


async def draft_names_a_removed_subject(engine: AsyncEngine) -> None:
    flow_id, _ = await draft_of_advies(engine)
    await remove_subjects(engine)
    async with AsyncSession(engine) as session, session.begin():
        session.add(SubjectFlowRow(flow_id=flow_id, revision=3, subject_code="gone"))


async def several_changes_at_once(engine: AsyncEngine) -> None:
    await archive_skill_directly(engine, "passport")
    await archive_pool_directly(engine, "kantoor")
    await draft_requires_archived_skill(engine)
    await draft_names_a_removed_subject(engine)


@pytest.mark.parametrize(
    "history",
    [
        pytest.param(lambda e: archive_skill_directly(e, "passport"), id="skill-archived-since"),
        pytest.param(lambda e: archive_pool_directly(e, "kantoor"), id="pool-archived-since"),
        pytest.param(remove_subjects, id="subject-removed-since"),
        pytest.param(draft_requires_archived_skill, id="draft-on-archived-skill"),
        pytest.param(draft_draws_on_archived_pool, id="draft-on-archived-pool"),
        pytest.param(draft_names_a_removed_subject, id="draft-with-removed-subject"),
        pytest.param(several_changes_at_once, id="all-of-them"),
    ],
)
async def test_what_time_left_behind_still_exports_and_imports_again(
    source: AsyncEngine, target: AsyncEngine, history: Any
) -> None:
    await history(source)
    exported = await export(source)

    applied = await transfer(source, target)

    assert applied.plan.refusals == (), applied.plan.refusals
    assert applied.applied
    assert await export(target) == exported


async def test_a_restored_archive_carries_the_time_of_the_apply(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    await archive_skill_directly(source, "passport")

    await transfer(source, target)

    async with AsyncSession(target) as session:
        stamped = await session.scalar(
            select(SkillRow.archived_at).where(SkillRow.code == "passport")
        )
    assert stamped is not None
    assert stamped.replace(tzinfo=UTC) == CLOCK


# --- a counted pool without a code is hidden: scope and names ---------------------------------


async def forget_pool_code(engine: AsyncEngine, organisation: str, name: str) -> None:
    """Store a counted pool without a code, as an image from before migration 0110 does."""
    async with AsyncSession(engine) as session, session.begin():
        owner = await session.scalar(
            select(OrganisationRow).where(OrganisationRow.slug == organisation)
        )
        assert owner is not None
        session.add(CapacityPoolRow(organisation_id=owner.id, name=name, mode="counted"))


async def test_an_organisations_export_is_not_stopped_by_another_organisations_pool(
    source: AsyncEngine,
) -> None:
    await forget_pool_code(source, "beta", "Verborgen")

    exported = await service(source).export(HOST, Scope("acme"), None, secrets=False)

    assert b"acme" in exported
    with pytest.raises(DomainError) as everything:
        await service(source).export(HOST, Scope(), None, secrets=False)
    with pytest.raises(DomainError) as beta:
        await service(source).export(HOST, Scope("beta"), None, secrets=False)
    assert everything.value.code is ErrorCode.CONFIGURATION_POOL_WITHOUT_CODE
    assert (beta.value.code, str(beta.value.field).split("/")[0]) == (
        ErrorCode.CONFIGURATION_POOL_WITHOUT_CODE,
        "beta",
    )


async def test_the_error_of_an_organisations_export_names_only_its_own_pool(
    source: AsyncEngine,
) -> None:
    await forget_pool_code(source, "beta", "Verborgen")
    await forget_pool_code(source, "acme", "Ook verborgen")

    with pytest.raises(DomainError) as refused:
        await service(source).export(HOST, Scope("acme"), None, secrets=False)

    assert str(refused.value.field).startswith("acme/")


async def test_a_new_pool_cannot_take_the_name_of_a_pool_hidden_for_lack_of_a_code(
    source: AsyncEngine,
) -> None:
    await forget_pool_code(source, "acme", "Verborgen")
    data = organisation_document(
        "acme", capacity_pools=[{"code": "nieuw", "name": " VERBORGEN ", "guaranteed_skills": []}]
    )

    plan = await service(source).plan(HOST, encode(data), None)

    assert [(r.code, r.field) for r in plan.refusals] == [(ErrorCode.SLUG_TAKEN, "pool_name")]
    assert plan.left_out == 1  # the preview warns that one pool is left out


async def test_the_name_of_an_archived_hidden_pool_is_free(source: AsyncEngine) -> None:
    await forget_pool_code(source, "acme", "Verborgen")
    async with AsyncSession(source) as session, session.begin():
        hidden = await session.scalar(
            select(CapacityPoolRow).where(CapacityPoolRow.name == "Verborgen")
        )
        assert hidden is not None
        hidden.archived_at = NOW
    data = organisation_document(
        "acme", capacity_pools=[{"code": "nieuw", "name": "Verborgen", "guaranteed_skills": []}]
    )

    plan = await service(source).plan(HOST, encode(data), None)

    assert plan.refusals == ()


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
    assert await count(source, ServiceFlowRow) == 5
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
    acme["capacity_pools"] = [p for p in acme["capacity_pools"] if p["code"] != "kantoor"]
    for group in acme["groups"]:
        group["flows"] = [f for f in group.get("flows", []) if f["code"] == "advies"]

    plan = await service(source).plan(HOST, encode(exported), None)

    lines = plan.lines()
    assert plan.refusals == ()
    assert "archive service_flow acme/helpdesk/appointment: state" in lines  # booked
    assert "archive service_flow acme/loket/telefoon: state" in lines  # it has a published revision
    assert "archive skill acme/passport: archived" in lines  # a person holds it
    assert "delete skill acme/old" in lines  # archived and unused
    assert "archive capacity_pool acme/kantoor: archived" in lines

    result = await service(source).apply(HOST, encode(exported), None, plan.digest)

    assert result.applied
    async with AsyncSession(source) as session:
        appointment = await session.scalar(
            select(ServiceFlowRow).where(ServiceFlowRow.code == "appointment")
        )
        assert appointment is not None
        assert appointment.state == "archived"
        assert await session.get(FlowRevisionRow, (appointment.id, 1)) is not None
        telefoon = await session.scalar(
            select(ServiceFlowRow).where(ServiceFlowRow.code == "telefoon")
        )
        assert telefoon is not None
        assert telefoon.state == "archived"  # a flow with a published revision is never deleted
        passport = await session.scalar(select(SkillRow).where(SkillRow.code == "passport"))
        assert passport is not None
        assert passport.archived_at is not None
        assert await session.scalar(select(SkillRow).where(SkillRow.code == "old")) is None
        kantoor = await session.scalar(
            select(CapacityPoolRow).where(CapacityPoolRow.code == "kantoor")
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


async def test_a_draft_is_published_and_the_next_one_added_in_order(source: AsyncEngine) -> None:
    exported = await export(source)
    helpdesk = exported["organisations"][0]["groups"][0]
    advies = next(f for f in helpdesk["flows"] if f["code"] == "advies")
    published = advies["revisions"][2]
    published["published_at"] = "2026-11-08T13:00:00+00:00"
    fresh = {
        **published,
        "revision": 4,
        "published_at": None,
        "labels": {"nl": "Vier", "en": "Four"},
    }
    advies["revisions"].append(fresh)
    advies["current_revision"], advies["draft_revision"] = 3, 4

    plan = await service(source).plan(HOST, encode(exported), None)
    result = await service(source).apply(HOST, encode(exported), None, plan.digest)

    assert plan.refusals == ()
    assert result.applied
    async with AsyncSession(source) as session:
        flow = await session.scalar(select(ServiceFlowRow).where(ServiceFlowRow.code == "advies"))
        assert flow is not None
        rows = {
            row.revision: row
            for row in await session.scalars(
                select(FlowRevisionRow).where(FlowRevisionRow.flow_id == flow.id)
            )
        }
        assert sorted(rows) == [1, 2, 3, 4]  # 1 and 2 are untouched
        assert rows[3].published_at is not None
        assert rows[3].published_by is None
        assert rows[4].published_at is None
        assert (flow.current_revision, flow.draft_revision) == (3, 4)


async def test_a_draft_is_replaced_in_place(source: AsyncEngine) -> None:
    exported = await export(source)
    helpdesk = exported["organisations"][0]["groups"][0]
    advies = next(f for f in helpdesk["flows"] if f["code"] == "advies")
    advies["revisions"][2]["labels"] = {"nl": "Drie", "en": "Three"}

    result = await service(source).apply(HOST, encode(exported), None, None)

    assert result.applied
    async with AsyncSession(source) as session:
        draft = await session.scalar(
            select(FlowRevisionRow)
            .join(ServiceFlowRow)
            .where(ServiceFlowRow.code == "advies", FlowRevisionRow.revision == 3)
        )
        assert draft is not None
        assert json.loads(draft.labels) == {"nl": "Drie", "en": "Three"}


# --- references and counts --------------------------------------------------------------


async def test_a_flow_cannot_use_the_legacy_pool_of_another_service_through_a_document(
    source: AsyncEngine,
) -> None:
    exported = await export(source)
    helpdesk = exported["organisations"][0]["groups"][0]
    advies = next(f for f in helpdesk["flows"] if f["code"] == "advies")
    advies["revisions"][2]["capacity_pool"] = None
    advies["revisions"][2]["legacy_pool_of"] = "loket"

    plan = await service(source).plan(HOST, encode(exported), None)

    assert [(r.code, r.field) for r in plan.refusals] == [
        (ErrorCode.INVALID_CONFIGURATION, "legacy_pool_of")
    ]


async def test_the_writer_resolves_a_legacy_pool_only_for_the_flows_own_service(
    source: AsyncEngine,
) -> None:
    change = planned(
        "flow_revision",
        ("acme", "helpdesk", "advies", "3"),
        ChangeAction.UPDATE,
        {**revision_json(), "legacy_pool_of": "loket"},
        key=("3",),
        organisation="acme",
        group="helpdesk",
    )

    async with AsyncSession(source) as session, session.begin():
        with pytest.raises(DomainError) as refused:
            await write_flow_revision(session, None, change)

    assert (refused.value.code, refused.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "legacy_pool_of",
    )


async def test_a_counted_pool_without_a_code_cannot_be_exported_but_does_not_stop_a_plan(
    source: AsyncEngine,
) -> None:
    exported = await export(source)
    async with AsyncSession(source) as session, session.begin():
        acme = await session.scalar(select(OrganisationRow).where(OrganisationRow.slug == "acme"))
        assert acme is not None
        pool = CapacityPoolRow(organisation_id=acme.id, name="Oud", mode="counted")
        session.add(pool)
        await session.flush()
        named = f"acme/{pool.id}"

    with pytest.raises(DomainError) as refused:
        await service(source).export(HOST, Scope(), None, secrets=False)
    plan = await service(source).plan(HOST, encode(exported), None)

    assert (refused.value.code, refused.value.field) == (
        ErrorCode.CONFIGURATION_POOL_WITHOUT_CODE,
        named,
    )
    assert plan.refusals == ()
    assert plan.changes == ()  # the pool has no address, so a document leaves it alone


async def test_the_usage_facts_name_what_a_deletion_would_break(source: AsyncEngine) -> None:
    await book_appointment(source)
    async with AsyncSession(source) as session:
        usage = await read_usage(session, NOW, frozenset())

    assert usage.kept["skill"] == frozenset({("acme", "passport")})
    assert usage.kept["capacity_pool"] == frozenset({("acme", "kantoor")})
    assert ("acme", "helpdesk", "appointment") in usage.kept["service_flow"]  # booked
    assert ("acme", "helpdesk", "advies") in usage.kept["service_flow"]  # published


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


async def apply_document(engine: AsyncEngine, data: dict[str, Any]) -> Any:
    """Apply a document to the engine; it must be accepted."""
    plan = await service(engine).plan(HOST, encode(data), None)
    assert plan.refusals == (), plan.refusals
    result = await service(engine).apply(HOST, encode(data), None, plan.digest)
    assert result.applied
    return result


async def flows_of_group(engine: AsyncEngine, slug: str) -> dict[str, tuple[str, bool]]:
    """Return the flows of a group by code, as ``(state, legacy revision 1)``."""
    async with AsyncSession(engine) as session:
        rows = (
            await session.execute(
                select(ServiceFlowRow, FlowRevisionRow)
                .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
                .join(FlowRevisionRow, FlowRevisionRow.flow_id == ServiceFlowRow.id)
                .where(GroupRow.slug == slug, FlowRevisionRow.revision == 1)
            )
        ).all()
    return {flow.code: (flow.state, revision.legacy) for flow, revision in rows}


async def legacy_pools(engine: AsyncEngine, slug: str) -> int:
    """Return how many pools wrap the group."""
    async with AsyncSession(engine) as session:
        return len(
            list(
                await session.scalars(
                    select(CapacityPoolRow.id)
                    .join(GroupRow, GroupRow.id == CapacityPoolRow.group_id)
                    .where(GroupRow.slug == slug)
                )
            )
        )


async def test_a_group_created_by_a_document_without_flows_gets_the_default_set(
    target: AsyncEngine,
) -> None:
    await apply_document(target, organisation_document("acme", group_json("nieuw")))

    assert await legacy_pools(target, "nieuw") == 1
    assert await flows_of_group(target, "nieuw") == {"appointment": ("active", True)}


async def test_a_group_created_with_its_own_flows_gets_those_plus_what_is_still_missing(
    target: AsyncEngine,
) -> None:
    own = flow_json(
        "advies",
        revision_json(1, published_at="2026-11-01T10:00:00+00:00", capacity_pool="loket"),
        current_revision=1,
        draft_revision=None,
    )
    await apply_document(
        target,
        organisation_document(
            "acme",
            group_json("nieuw", flows=[own]),
            capacity_pools=[{"code": "loket", "name": "Loket", "guaranteed_skills": []}],
        ),
    )

    assert await legacy_pools(target, "nieuw") == 1  # the document had none: the rule adds it
    assert await flows_of_group(target, "nieuw") == {
        "advies": ("draft", False),  # the document's own flow, as it states it
        "appointment": ("active", True),  # what the group still lacked
    }


# --- the default entry path of a group, in either order -------------------------------------


def flow_change(code: str, action: ChangeAction, **values: Any) -> Any:
    """Return a change of a flow of group ``helpdesk``."""
    return planned(
        "service_flow",
        ("acme", "helpdesk", code),
        action,
        values,
        organisation="acme",
        group="helpdesk",
    )


def revision_change(code: str, number: int) -> Any:
    """Return the creation of a published revision of a flow of group ``helpdesk``."""
    return planned(
        "flow_revision",
        ("acme", "helpdesk", code, str(number)),
        ChangeAction.CREATE,
        {**revision_json(), "published_at": "2026-11-01T10:00:00+00:00"},
        key=(str(number),),
        organisation="acme",
        group="helpdesk",
    )


GAINS = flow_change(
    "second",
    ChangeAction.CREATE,
    state="active",
    default_for=["appointment"],
    current_revision=1,
    draft_revision=None,
)
LOSES = flow_change("appointment", ChangeAction.UPDATE, default_for=[])
ARCHIVES = flow_change("appointment", ChangeAction.ARCHIVE, state="archived")


def codes_of(changes: list[Any]) -> list[tuple[str, str]]:
    return [(c.kind, c.key[0]) for c in changes]


def test_the_flows_that_give_up_a_default_are_written_before_the_one_that_takes_it() -> None:
    plan_order = [GAINS, revision_change("second", 1), LOSES, ARCHIVES]

    ordered = order_flow_changes(plan_order)

    assert codes_of(ordered) == [
        ("service_flow", "appointment"),
        ("service_flow", "appointment"),
        ("service_flow", "second"),
        ("flow_revision", "1"),
    ]


def test_changes_of_other_groups_and_other_kinds_keep_their_place() -> None:
    elsewhere = planned(
        "service_flow", ("acme", "loket", "x"), ChangeAction.CREATE, {"state": "active"}
    )
    skill_change = planned("skill", ("acme", "s"), ChangeAction.CREATE, {"archived": False})
    plan_order = [skill_change, GAINS, LOSES, elsewhere, skill_change]

    ordered = order_flow_changes(plan_order)

    assert ordered == [skill_change, LOSES, GAINS, elsewhere, skill_change]


async def default_holder(engine: AsyncEngine) -> list[tuple[str, str]]:
    """Return the active flows of ``helpdesk`` that are the default of the appointment path."""
    async with AsyncSession(engine) as session:
        rows = await session.execute(
            select(ServiceFlowRow.code, ServiceFlowRow.state)
            .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
            .where(GroupRow.slug == "helpdesk", ServiceFlowRow.default_appointment.is_(True))
            .order_by(ServiceFlowRow.code)
        )
        return [(code, state) for code, state in rows.all()]


@pytest.mark.parametrize("first", ["gains", "loses"])
async def test_a_default_can_move_to_a_new_flow_whatever_order_the_document_lists_them_in(
    source: AsyncEngine, first: str
) -> None:
    plan_order = [GAINS, revision_change("second", 1), LOSES]
    if first == "loses":
        plan_order = [LOSES, GAINS, revision_change("second", 1)]

    async with SqlUnitOfWork(source, None).locked() as tx:
        await tx.apply_configuration(plan_order)

    assert await default_holder(source) == [("second", "active")]


async def test_a_default_that_is_taken_twice_is_a_document_error_and_not_a_server_error(
    source: AsyncEngine,
) -> None:
    taken = flow_change(
        "other",
        ChangeAction.CREATE,
        state="active",
        default_for=["appointment"],
        current_revision=1,
        draft_revision=None,
    )

    with pytest.raises(DomainError) as refused:
        async with SqlUnitOfWork(source, None).locked() as tx:
            await tx.apply_configuration([taken])

    assert (refused.value.code, refused.value.field) == (
        ErrorCode.CONFIGURATION_OBJECT_EXISTS,
        "default_for",
    )
    assert await default_holder(source) == [("appointment", "active")]  # nothing was written
