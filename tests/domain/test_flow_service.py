"""FlowService (ADR 0121 step 2d): authorisation, drafts, publishing, activation and archiving."""

from dataclasses import replace

import pytest

from app.domain import audit
from app.domain import service_flows as flows
from app.domain.admin import AdminService
from app.domain.authorization import (
    CustomRole,
    Grant,
    Permission,
    Principal,
    Role,
    Scope,
)
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_service import FlowService
from app.domain.routing import GroupRouting, Subject
from app.domain.service_flows import FlowState, IntakeMode
from app.domain.skills import Skill
from app.domain.staff import Membership, StaffMember
from tests.fakes import clock as fake_clock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW
from tests.support.flow_seed import draft

EVERYTHING = Scope(everything=True)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def make(uow: InMemoryUnitOfWork, limit: int = 3) -> FlowService:
    clock = fake_clock.FixedClock(NOW)
    return FlowService(uow, clock, AdminService(uow, clock), lambda: limit)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> FlowService:
    return make(uow)


def details(uow: InMemoryUnitOfWork) -> list[str]:
    return [e.detail for e in uow.state.audit if e.outcome is audit.Outcome.SUCCESS]


def denials(uow: InMemoryUnitOfWork) -> list[audit.AuditEvent]:
    return [e for e in uow.state.audit if e.outcome is audit.Outcome.DENIED]


async def seed(
    uow: InMemoryUnitOfWork, organisation_id: int = 1, code: str = "x"
) -> tuple[int, int]:
    """Store a skill and a counted pool (guaranteeing it) of the organisation; return their ids."""
    async with uow.locked() as tx:
        skill = await tx.save_skill(
            EVERYTHING, Skill(None, organisation_id, f"s-{code}", {"nl": "S", "en": "S"})
        )
        pool = await tx.save_capacity_pool(
            EVERYTHING,
            CapacityPool(
                None,
                organisation_id,
                f"Pool {code}",
                PoolMode.COUNTED,
                frozenset({skill.id or 0}),
                code=f"pool-{code}",
            ),
        )
    return skill.id or 0, pool.id or 0


async def person(uow: InMemoryUnitOfWork, subject: str) -> None:
    """Store a staff record for ``subject``: whoever publishes is a person with a record."""
    async with uow.locked() as tx:
        await tx.save_staff(
            StaffMember(
                subject,
                "Staff",
                frozenset(),
                frozenset({Membership("org_admin", "demo")}),
                NOW,
                NOW,
                NOW,
            )
        )


def refused(error: pytest.ExceptionInfo[DomainError]) -> ErrorCode:
    return error.value.code


async def test_every_action_refused_by_the_matrix_is_audited_and_changes_nothing(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "advies", draft(pool))
    before = len(details(uow))
    actions = [
        lambda who: service.overview(who, "demo"),
        lambda who: service.form(who, "demo"),
        lambda who: service.page(who, "demo", "advies"),
        lambda who: service.create(who, "demo", "ander", draft(pool)),
        lambda who: service.save(who, "demo", "advies", draft(pool)),
        lambda who: service.prepare(who, "demo", "advies"),
        lambda who: service.publish(who, "demo", "advies"),
        lambda who: service.discard(who, "demo", "advies"),
        lambda who: service.activate(who, "demo", "advies"),
        lambda who: service.archive(who, "demo", "advies"),
    ]

    for who in (GROUP_ADMIN, OUTSIDER, AUDITOR):
        for action in actions:
            with pytest.raises(DomainError) as error:
                await action(who)
            assert error.value.code is ErrorCode.NOT_PERMITTED

    assert len(denials(uow)) == 3 * len(actions)
    assert len(details(uow)) == before
    (flow,) = uow.state.flows.values()
    assert flow.code == "advies"


async def test_a_flow_goes_from_draft_to_published_with_audit_details_of_codes_only(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    await person(uow, ORG_ADMIN.subject)
    _, pool = await seed(uow)

    created = await service.create(ORG_ADMIN, "demo", "advies", draft(pool))
    saved = await service.save(
        ORG_ADMIN, "demo", "advies", draft(pool, labels={"nl": "Geheime naam", "en": "Secret"})
    )
    published = await service.publish(ORG_ADMIN, "demo", "advies")

    assert created.state is FlowState.DRAFT
    assert created.draft is not None
    assert created.current is None
    assert saved.draft is not None
    assert saved.draft.labels["nl"] == "Geheime naam"
    assert published.current is not None
    assert published.current.published_at == NOW
    assert published.draft is None
    assert details(uow) == [
        "flow.created,flow:advies,revision:1",
        "flow.draft_saved,flow:advies,revision:1",
        "flow.published,flow:advies,revision:1",
    ]
    assert "Geheime" not in repr(uow.state.audit)


async def test_a_new_revision_is_prepared_from_the_published_one_and_published_over_it(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    await person(uow, ORG_ADMIN.subject)
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "advies", draft(pool))
    await service.publish(ORG_ADMIN, "demo", "advies")

    prepared = await service.prepare(ORG_ADMIN, "demo", "advies")

    assert prepared.draft is not None
    assert prepared.current is not None
    assert prepared.draft.revision == 2
    assert prepared.draft.labels == prepared.current.labels
    with pytest.raises(DomainError) as twice:
        await service.prepare(ORG_ADMIN, "demo", "advies")
    assert twice.value.field == "state"
    again = await service.publish(ORG_ADMIN, "demo", "advies")
    assert again.current is not None
    assert again.current.revision == 2
    assert details(uow)[-2:] == [
        "flow.draft_saved,flow:advies,revision:2",
        "flow.published,flow:advies,revision:2",
    ]


async def test_discarding_a_draft_keeps_the_published_revision_or_removes_a_new_flow(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    await person(uow, ORG_ADMIN.subject)
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "nooit", draft(pool))
    await service.create(ORG_ADMIN, "demo", "advies", draft(pool))
    await service.publish(ORG_ADMIN, "demo", "advies")
    await service.prepare(ORG_ADMIN, "demo", "advies")

    assert await service.discard(ORG_ADMIN, "demo", "nooit") is True
    assert await service.discard(ORG_ADMIN, "demo", "advies") is False

    (flow,) = uow.state.flows.values()
    assert flow.code == "advies"
    assert flow.current is not None
    assert flow.draft is None
    with pytest.raises(DomainError) as none:
        await service.discard(ORG_ADMIN, "demo", "advies")
    assert (none.value.code, none.value.field) == (ErrorCode.CONFIGURATION_OBJECT_MISSING, "draft")
    assert details(uow)[-2:] == [
        "flow.draft_discarded,flow:nooit,revision:1",
        "flow.draft_discarded,flow:advies,revision:2",
    ]


async def test_activation_is_refused_with_the_checklist_while_something_is_not_built(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    await person(uow, ORG_ADMIN.subject)
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "advies", draft(pool))
    await service.publish(ORG_ADMIN, "demo", "advies")

    outcome = await service.activate(ORG_ADMIN, "demo", "advies")

    assert outcome.activated is False
    missing = [i for i in outcome.items if not i.ok]
    assert [(i.code, i.reason) for i in missing] == [
        (flows.ReadinessCheck.RETENTION, flows.ReadinessReason.NOT_BUILT_YET)
    ]
    (flow,) = uow.state.flows.values()
    assert flow.state is FlowState.DRAFT
    assert not any(d.startswith("flow.activated") for d in details(uow))


async def test_activation_needs_a_published_revision(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "advies", draft(pool))

    with pytest.raises(DomainError) as error:
        await service.activate(ORG_ADMIN, "demo", "advies")

    assert (error.value.code, error.value.field) == (ErrorCode.INVALID_CONFIGURATION, "state")


async def test_a_flow_that_is_ready_is_activated_and_archived(
    service: FlowService, uow: InMemoryUnitOfWork, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flows, "RETENTION_BUILT", True)
    await person(uow, ORG_ADMIN.subject)
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "advies", draft(pool))
    await service.publish(ORG_ADMIN, "demo", "advies")

    outcome = await service.activate(ORG_ADMIN, "demo", "advies")
    archived = await service.archive(ORG_ADMIN, "demo", "advies")

    assert outcome.activated is True
    assert flows.can_activate(outcome.items)
    assert archived.state is FlowState.ARCHIVED
    assert details(uow)[-2:] == [
        "flow.activated,flow:advies,revision:1",
        "flow.archived,flow:advies,revision:1",
    ]
    with pytest.raises(DomainError) as again:
        await service.archive(ORG_ADMIN, "demo", "advies")
    assert again.value.field == "state"
    with pytest.raises(DomainError) as edit:
        await service.prepare(ORG_ADMIN, "demo", "advies")
    assert edit.value.field == "state"


async def test_a_flow_in_draft_state_is_not_archived_but_discarded(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    await person(uow, ORG_ADMIN.subject)
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "advies", draft(pool))
    await service.publish(ORG_ADMIN, "demo", "advies")

    with pytest.raises(DomainError) as error:
        await service.archive(ORG_ADMIN, "demo", "advies")

    assert (error.value.code, error.value.field) == (ErrorCode.INVALID_CONFIGURATION, "state")


async def test_a_second_active_default_is_refused_when_the_flow_is_activated(
    service: FlowService, uow: InMemoryUnitOfWork, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flows, "RETENTION_BUILT", True)
    await person(uow, ORG_ADMIN.subject)
    _, pool = await seed(uow)
    default = frozenset({IntakeMode.APPOINTMENT})
    for code in ("een", "twee"):
        await service.create(ORG_ADMIN, "demo", code, draft(pool, default_for=default))
        await service.publish(ORG_ADMIN, "demo", code)
    await service.activate(ORG_ADMIN, "demo", "een")

    with pytest.raises(DomainError) as error:
        await service.activate(ORG_ADMIN, "demo", "twee")

    assert (error.value.code, error.value.field) == (
        ErrorCode.CONFIGURATION_OBJECT_EXISTS,
        "default_for",
    )


async def test_a_draft_that_is_only_incomplete_is_saved_but_a_wrong_value_is_refused(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    _, pool = await seed(uow)
    empty = draft(
        None,
        intake_modes=frozenset(),
        interaction_kind=None,
        duration_minutes=None,
        dispatch=None,
        handling_action=None,
        completion=None,
    )

    stored = await service.create(ORG_ADMIN, "demo", "leeg", empty)

    assert stored.draft is not None
    assert stored.draft.interaction_kind is None
    wrong = {
        "labels": ({"nl": "x", "en": ""}, "labels"),
        "duration_minutes": (7, "duration_minutes"),
        "dispatch": (flows.DispatchKind.CONTACT_CENTER, "dispatch"),
        "completion": (flows.CompletionPolicy.PROVIDER_EVENT, "completion"),
        "handling_action": (flows.HandlingAction.ASSIGN_WORKSTATION, "handling_action"),
    }
    for change, (value, field) in wrong.items():
        extra = {"interaction_kind": flows.InteractionKind.IN_PERSON} if "action" in change else {}
        with pytest.raises(DomainError) as error:
            await service.save(
                ORG_ADMIN, "demo", "leeg", replace(empty, **{change: value}, **extra)
            )
        assert (error.value.code, error.value.field) == (ErrorCode.INVALID_CONFIGURATION, field)
    with pytest.raises(DomainError) as unknown:
        await service.create(
            ORG_ADMIN,
            "demo",
            "weg",
            draft(pool, interaction_kind=None, handling_action=flows.HandlingAction.CALL),
        )
    assert unknown.value.field == "handling_action"
    del pool


async def test_a_default_path_the_flow_does_not_offer_is_refused(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    _, pool = await seed(uow)

    with pytest.raises(DomainError) as error:
        await service.create(
            ORG_ADMIN, "demo", "advies", draft(pool, default_for=frozenset({IntakeMode.ON_DEMAND}))
        )

    assert error.value.field == "default_for"


async def test_code_rules_the_reserved_new_and_taken_codes(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "advies", draft(pool))

    with pytest.raises(DomainError) as taken:
        await service.create(ORG_ADMIN, "demo", "advies", draft(pool))
    with pytest.raises(DomainError) as reserved:
        await service.create(ORG_ADMIN, "demo", "new", draft(pool))
    with pytest.raises(DomainError) as bad:
        await service.create(ORG_ADMIN, "demo", "Not A Code", draft(pool))

    assert taken.value.code is ErrorCode.SLUG_TAKEN
    assert (reserved.value.code, reserved.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "flow_code",
    )
    assert (bad.value.code, bad.value.field) == (ErrorCode.INVALID_CONFIGURATION, "flow_code")


async def test_the_limit_counts_flows_not_archived(
    uow: InMemoryUnitOfWork,
) -> None:
    service = make(uow, limit=2)
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "een", draft(pool))
    await service.create(ORG_ADMIN, "demo", "twee", draft(pool))

    with pytest.raises(DomainError) as error:
        await service.create(ORG_ADMIN, "demo", "drie", draft(pool))
    overview = await service.overview(ORG_ADMIN, "demo")

    assert error.value.field == "flow_limit"
    assert overview.can_add is False
    assert overview.limit == 2


async def test_a_pool_that_is_not_counted_or_not_the_organisations_is_refused(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    async with uow.locked() as tx:
        legacy = await tx.save_capacity_pool(
            EVERYTHING, CapacityPool(None, 1, "Eigen bezetting", PoolMode.LEGACY_GROUP, group_id=1)
        )
    _, theirs = await seed(uow, organisation_id=2, code="y")

    for pool_id, code in (
        (legacy.id, ErrorCode.INVALID_CONFIGURATION),
        (theirs, ErrorCode.CONFIGURATION_OUT_OF_SCOPE),
    ):
        with pytest.raises(DomainError) as error:
            await service.create(ORG_ADMIN, "demo", "advies", draft(pool_id))
        assert (error.value.code, error.value.field) == (code, "capacity_pool_id")
    assert not uow.state.flows


async def test_a_skill_of_another_organisation_or_a_missing_one_is_refused(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    _, pool = await seed(uow)
    their_skill, _ = await seed(uow, organisation_id=2, code="y")

    for skill in (their_skill, 999):
        with pytest.raises(DomainError) as error:
            await service.create(
                ORG_ADMIN, "demo", "advies", draft(pool, required_skill_ids=frozenset({skill}))
            )
        assert error.value.field == "required_skill_ids"
    assert not uow.state.flows


async def test_a_flow_of_another_service_cannot_be_read_or_changed(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    _, mine = await seed(uow)
    _, theirs = await seed(uow, organisation_id=2, code="y")
    await service.create(OUTSIDER, "andere-team", "geheim", draft(theirs))
    await service.create(ORG_ADMIN, "demo", "advies", draft(mine))

    for action in (
        lambda: service.page(ORG_ADMIN, "demo", "geheim"),
        lambda: service.save(ORG_ADMIN, "demo", "geheim", draft(mine)),
        lambda: service.discard(ORG_ADMIN, "demo", "geheim"),
        lambda: service.publish(ORG_ADMIN, "demo", "geheim"),
        lambda: service.archive(ORG_ADMIN, "demo", "geheim"),
    ):
        with pytest.raises(DomainError) as error:
            await action()
        assert (error.value.code, error.value.field) == (
            ErrorCode.CONFIGURATION_OBJECT_MISSING,
            "flow_code",
        )
    for action in (
        lambda: service.page(ORG_ADMIN, "andere-team", "geheim"),
        lambda: service.overview(OUTSIDER, "demo"),
        lambda: service.page(OUTSIDER, "demo", "advies"),
    ):
        with pytest.raises(DomainError) as denied:
            await action()
        assert denied.value.code is ErrorCode.NOT_PERMITTED
    assert len(denials(uow)) == 3


async def test_the_overview_lists_flows_by_state_with_their_pool_and_skills(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    await person(uow, ORG_ADMIN.subject)
    skill, pool = await seed(uow)
    await service.create(
        ORG_ADMIN, "demo", "advies", draft(pool, required_skill_ids=frozenset({skill}))
    )
    await service.publish(ORG_ADMIN, "demo", "advies")
    await service.prepare(ORG_ADMIN, "demo", "advies")

    open_ = await service.overview(ORG_ADMIN, "demo")
    archived = await service.overview(ORG_ADMIN, "demo", archived=True)

    (line,) = open_.lines
    assert line.flow.draft is not None
    assert line.flow.current is not None
    assert line.pool is not None
    assert line.pool.id == pool
    assert [s.code for s in line.skills] == ["s-x"]
    assert archived.lines == ()
    assert open_.can_add is True


async def test_the_form_offers_the_organisations_counted_pools_and_skills_only(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    skill, pool = await seed(uow)
    await seed(uow, organisation_id=2, code="y")
    async with uow.locked() as tx:
        await tx.save_capacity_pool(
            EVERYTHING, CapacityPool(None, 1, "Eigen bezetting", PoolMode.LEGACY_GROUP, group_id=1)
        )
        archived = await tx.save_skill(EVERYTHING, Skill(None, 1, "oud", {"nl": "O", "en": "O"}))
        await tx.archive_skill(EVERYTHING, archived.id or 0, NOW)
        await tx.save_group_routing(
            1, GroupRouting(subjects=(Subject.create("contract", {"nl": "C", "en": "C"}),))
        )

    form = await service.form(ORG_ADMIN, "demo")

    assert [p.id for p in form.choices.pools] == [pool]
    assert [s.id for s in form.choices.skills] == [skill]
    assert [s.code for s in form.choices.subjects] == ["contract"]


async def test_a_group_scoped_caller_is_offered_the_same_and_only_the_groups_own_legacy_pool(
    uow: InMemoryUnitOfWork,
) -> None:
    custom = CustomRole(1, Role.GROUP_ADMIN, frozenset({Permission.GROUP_EDIT}), "c", "Rol", "Role")
    caller = Principal(
        "g-3", "G3", frozenset({Grant(custom.key, "demo", "demo")}), frozenset({custom})
    )
    service = make(uow)
    skill, pool = await seed(uow)
    await seed(uow, organisation_id=2, code="y")

    form = await service.form(caller, "demo")

    assert [p.id for p in form.choices.pools] == [pool]
    assert [s.id for s in form.choices.skills] == [skill]
    with pytest.raises(DomainError) as other:
        await service.form(caller, "andere-team")
    assert other.value.code is ErrorCode.NOT_PERMITTED


async def test_a_legacy_flow_is_listed_and_every_action_on_it_answers_like_an_unknown_flow(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    await person(uow, ORG_ADMIN.subject)
    async with uow.locked() as tx:
        await tx.ensure_default_flows(1)

    listed = await service.overview(ORG_ADMIN, "demo")

    (line,) = listed.lines
    assert line.legacy is True
    assert line.flow.code == "appointment"
    assert listed.can_add is True  # a legacy flow does not count against the limit
    for action in (
        lambda: service.page(ORG_ADMIN, "demo", "appointment"),
        lambda: service.save(ORG_ADMIN, "demo", "appointment", draft(1)),
        lambda: service.prepare(ORG_ADMIN, "demo", "appointment"),
        lambda: service.publish(ORG_ADMIN, "demo", "appointment"),
        lambda: service.discard(ORG_ADMIN, "demo", "appointment"),
        lambda: service.activate(ORG_ADMIN, "demo", "appointment"),
        lambda: service.archive(ORG_ADMIN, "demo", "appointment"),
    ):
        with pytest.raises(DomainError) as error:
            await action()
        assert (error.value.code, error.value.field) == (
            ErrorCode.CONFIGURATION_OBJECT_MISSING,
            "flow_code",
        )


async def test_publishing_needs_a_person_with_a_staff_record(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    _, pool = await seed(uow)
    await service.create(SUPER, "demo", "advies", draft(pool))

    with pytest.raises(DomainError) as error:
        await service.publish(SUPER, "demo", "advies")

    assert error.value.code is ErrorCode.STAFF_NOT_FOUND


async def test_the_page_judges_the_draft_and_the_activation_the_current_revision(
    service: FlowService, uow: InMemoryUnitOfWork
) -> None:
    await person(uow, ORG_ADMIN.subject)
    _, pool = await seed(uow)
    await service.create(ORG_ADMIN, "demo", "advies", draft(pool))
    new = await service.page(ORG_ADMIN, "demo", "advies")
    await service.publish(ORG_ADMIN, "demo", "advies")
    await service.prepare(ORG_ADMIN, "demo", "advies")
    await service.save(
        ORG_ADMIN, "demo", "advies", draft(pool, interaction_kind=None, handling_action=None)
    )

    page = await service.page(ORG_ADMIN, "demo", "advies")

    assert new.activation is None
    assert new.can_activate is False
    assert page.editable is True
    assert page.shown is page.flow.draft
    assert [i.reason for i in page.items if not i.ok][0] is flows.ReadinessReason.KIND_MISSING
    assert page.activation is not None
    assert all(i.ok for i in page.activation[:3])
    assert page.can_activate is False  # retention is not built
