"""Contact-center codes of skills (ADR 0121 step 4b): mapped codes only, never a dropped skill.

The rules of ``app.domain.skills`` (``mapped_skill_codes``), the readiness reason
``skill_unmapped``, the portal's save of a code and the dispatch that sends the codes of the
skills a booking requires.
"""

from dataclasses import replace

import pytest

from app.domain.admin import AdminService
from app.domain.booking import MeetingRef
from app.domain.call_service import CallService
from app.domain.configuration_v1_group import _check_routing
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef
from app.domain.integration import IntegrationService
from app.domain.routing import SUBJECT_SOURCE, RouteVariable, RoutingChoice, Subject
from app.domain.routing_service import RoutingService
from app.domain.scheduling import Organisation, ScheduleRule
from app.domain.service_flows import (
    DispatchKind,
    FlowState,
    ReadinessCheck,
    ReadinessReason,
    ServiceFlow,
    check_code_kept,
    readiness,
)
from app.domain.services import BookingService
from app.domain.skill_service import SkillService
from app.domain.skills import MAX_JOINED_CODES, Skill, mapped_skill_codes, unmapped_skills
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo, webex_cc
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.admin import ORG_ADMIN, two_organisations
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, make_group, webex_routing, window
from tests.support.service_flows import legacy, pool, revision

GERMAN = Skill(10, 1, "german", {"nl": "Duits", "en": "German"}, contact_center_code="lang_de")
INTAKE = Skill(11, 1, "intake", {"nl": "Intake", "en": "Intake"})
SKILLS = {GERMAN.id or 0: GERMAN, INTAKE.id or 0: INTAKE}


def _refused(call: object) -> tuple[ErrorCode, str | None]:
    with pytest.raises(DomainError) as excinfo:
        call()  # type: ignore[operator]
    return excinfo.value.code, excinfo.value.field


def test_a_code_has_the_grammar_of_a_routing_value() -> None:
    assert GERMAN.contact_center_code == "lang_de"
    for bad in ("", "met spatie", "a" * 65, "x,y"):
        assert _refused(lambda bad=bad: replace(GERMAN, contact_center_code=bad)) == (
            ErrorCode.INVALID_CONFIGURATION,
            "contact_center_code",
        )


def test_only_mapped_codes_are_sent_sorted_and_an_unmapped_one_refuses() -> None:
    other = replace(INTAKE, contact_center_code="a.intake")
    assert mapped_skill_codes({10, 11}, {10: GERMAN, 11: other}) == ("a.intake", "lang_de")
    assert mapped_skill_codes(set(), SKILLS) == ()
    assert unmapped_skills({10, 11, 99}, SKILLS) == {11, 99}
    assert _refused(lambda: mapped_skill_codes({10, 11}, SKILLS)) == (
        ErrorCode.INTEGRATION_NOT_READY,
        "skill_codes",
    )


def test_the_joined_codes_fit_one_flow_variable() -> None:
    many = {
        i: Skill(i, 1, f"s{i}", {"nl": "x", "en": "x"}, contact_center_code=f"{i:02d}" + "c" * 62)
        for i in range(10)
    }
    assert len(",".join(c.contact_center_code or "" for c in many.values())) > MAX_JOINED_CODES
    assert _refused(lambda: mapped_skill_codes(many.keys(), many))[1] == "skill_codes"


def _skills_item(dispatch: DispatchKind, coded: frozenset[int], **changes: object) -> object:
    made = revision(dispatch=dispatch, **changes)
    items = readiness(
        made,
        pool(guaranteed=frozenset({10, 11})),
        offered_subjects=("renewal",),
        coded_skills=coded,
    )
    return next(i for i in items if i.code is ReadinessCheck.SKILLS).reason


def test_a_contact_center_flow_with_an_unmapped_skill_is_not_ready() -> None:
    contact_center = DispatchKind.CONTACT_CENTER
    needs = {"required_skill_ids": frozenset({10})}
    subject = {"subject_skill_ids": {"renewal": frozenset({11})}}

    assert _skills_item(contact_center, frozenset(), **needs) is ReadinessReason.SKILL_UNMAPPED
    assert _skills_item(contact_center, frozenset({10}), **needs) is None
    assert (
        _skills_item(contact_center, frozenset({10}), **subject) is ReadinessReason.SKILL_UNMAPPED
    )
    assert _skills_item(DispatchKind.OWN_QUEUE, frozenset(), **needs) is None
    assert _skills_item(contact_center, frozenset()) is None  # no skills, nothing to map


def test_a_legacy_flow_never_needs_a_code() -> None:
    items = readiness(legacy(dispatch=DispatchKind.CONTACT_CENTER), pool(), offered_subjects=())
    assert next(i for i in items if i.code is ReadinessCheck.SKILLS).ok


async def test_the_portal_saves_keeps_and_clears_a_code() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    clock = FixedClock(NOW)
    service = SkillService(uow, clock, AdminService(uow, clock), lambda: 10)
    first = await service.create(ORG_ADMIN, "demo", "german", "Duits", "German")
    second = await service.create(ORG_ADMIN, "demo", "intake", "Intake", "Intake")
    assert first.id is not None
    assert second.id is not None

    assert await service.relabel(ORG_ADMIN, "demo", first.id, "Duits", "German", " lang_de ")
    assert uow.state.skills[first.id].contact_center_code == "lang_de"
    assert not await service.relabel(ORG_ADMIN, "demo", first.id, "Duits", "German")
    assert await service.relabel(ORG_ADMIN, "demo", first.id, "Duits!", "German")
    assert uow.state.skills[first.id].contact_center_code == "lang_de"
    with pytest.raises(DomainError) as twin:
        await service.relabel(ORG_ADMIN, "demo", second.id, "Intake", "Intake", "lang_de")
    assert (twin.value.code, twin.value.field) == (ErrorCode.SLUG_TAKEN, "contact_center_code")
    assert await service.relabel(ORG_ADMIN, "demo", first.id, "Duits", "German", "")
    assert uow.state.skills[first.id].contact_center_code is None


async def _dispatch(*skills: Skill, flow: FlowRef | None, subject: str | None = None) -> object:
    """Dispatch one booking of a contact-center group made under ``flow``; return the task."""
    state = State(
        organisation_slugs={1: "demo"}, organisations={1: Organisation(1, "Demo", "demo")}
    )
    group = make_group(
        id=1,
        organisation_id=1,
        slug="balie",
        video_host_email="host@example.nl",
        contact_center=webex_routing("openplanner@example.biz", "openplanner-video", "team-1"),
    )
    state.groups[1] = group
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    state.skills = {s.id or 0: s for s in skills}
    state.flow_revisions[(50, 1)] = revision(
        flow_id=50,
        dispatch=DispatchKind.CONTACT_CENTER,
        required_skill_ids=frozenset({10}),
        subject_skill_ids={"renewal": frozenset({11})},
        published_at=NOW,
    )
    state.flow_revisions[(51, 1)] = legacy(flow_id=51, published_at=NOW)
    uow = InMemoryUnitOfWork(state)
    clock = FixedClock(NOW)
    receipt = await book(BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0))
    booking = replace(
        receipt.booking,
        meeting=MeetingRef("m-1", "https://site.webex.com/m"),
        flow=flow,
        routing=RoutingChoice(subject) if subject else None,
    )
    dispatcher = FakeDispatcher()
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    calls = CallService(uow, integration, FakeVideo(), webex_cc(dispatcher), lambda b, g: "x")
    await calls.ensure_task(booking, group)
    [task] = dispatcher.items
    return task


async def test_the_task_carries_the_codes_of_what_the_booking_requires() -> None:
    mapped = replace(INTAKE, contact_center_code="intake_1")

    plain = await _dispatch(GERMAN, mapped, flow=FlowRef(50, 1))
    with_subject = await _dispatch(GERMAN, mapped, flow=FlowRef(50, 1), subject="renewal")

    assert plain.skills == ("lang_de",)  # type: ignore[attr-defined]
    assert with_subject.skills == ("intake_1", "lang_de")  # type: ignore[attr-defined]


async def test_a_booking_without_skills_sends_no_codes() -> None:
    """Nothing changes for flows without skills: no flow, or a legacy wrapper."""
    for flow in (None, FlowRef(51, 1)):
        assert (await _dispatch(flow=flow)).skills == ()  # type: ignore[attr-defined]


async def test_an_unmapped_requirement_is_never_dropped_from_the_task() -> None:
    with pytest.raises(DomainError) as excinfo:
        await _dispatch(GERMAN, INTAKE, flow=FlowRef(50, 1), subject="renewal")
    assert (excinfo.value.code, excinfo.value.field) == (
        ErrorCode.INTEGRATION_NOT_READY,
        "skill_codes",
    )


async def test_a_new_routing_variable_may_not_take_the_name_of_the_codes() -> None:
    """The mapping form refuses ``openplannerSkills``; a stored one still loads (ADR 0116)."""
    uow = InMemoryUnitOfWork(two_organisations())
    clock = FixedClock(NOW)
    service = RoutingService(uow, clock, AdminService(uow, clock), lambda: 3)
    await service.add_subject(ORG_ADMIN, "demo", Subject.create("move", {"nl": "V", "en": "M"}))
    name = "openplannerSkills"

    with pytest.raises(DomainError) as excinfo:
        await service.set_mapping(ORG_ADMIN, "demo", SUBJECT_SOURCE, name, {"move": "MV"})

    assert (excinfo.value.code, excinfo.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "variable_name",
    )
    assert RouteVariable(name, (("move", "MV"),)).name == name


def test_a_configuration_document_may_not_map_onto_the_name_of_the_codes() -> None:
    routing = {
        "subjects": [{"code": "move", "labels": {"nl": "V", "en": "M"}}],
        "mapping": {"subject": {"name": "openplannerSkills", "values": {"move": "MV"}}},
    }

    assert _refused(lambda: _check_routing(routing, None)) == (  # type: ignore[arg-type]
        ErrorCode.INVALID_CONFIGURATION,
        "variable_name",
    )


async def test_a_code_a_contact_center_flow_needs_cannot_be_taken_away() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    clock = FixedClock(NOW)
    service = SkillService(uow, clock, AdminService(uow, clock), lambda: 10)
    skill = await service.create(ORG_ADMIN, "demo", "german", "Duits", "German")
    assert skill.id is not None
    await service.relabel(ORG_ADMIN, "demo", skill.id, "Duits", "German", "lang_de")
    uow.state.flows[60] = ServiceFlow(
        60,
        1,
        "cc",
        FlowState.ACTIVE,
        frozenset(),
        revision(
            flow_id=60,
            dispatch=DispatchKind.CONTACT_CENTER,
            required_skill_ids=frozenset({skill.id}),
            published_at=NOW,
        ),
    )

    with pytest.raises(DomainError) as excinfo:
        await service.relabel(ORG_ADMIN, "demo", skill.id, "Duits", "German", "")

    assert (excinfo.value.code, excinfo.value.field) == (
        ErrorCode.CONFIGURATION_OBJECT_IN_USE,
        "contact_center_code",
    )
    assert uow.state.skills[skill.id].contact_center_code == "lang_de"
    assert await service.relabel(ORG_ADMIN, "demo", skill.id, "Duits", "German", "lang_de2")
    uow.state.flows[60] = replace(uow.state.flows[60], state=FlowState.ARCHIVED)
    assert await service.relabel(ORG_ADMIN, "demo", skill.id, "Duits", "German", "")


def test_only_a_current_contact_center_revision_that_requires_the_skill_keeps_the_code() -> None:
    def flow(**changes: object) -> ServiceFlow:
        current = revision(
            flow_id=1,
            dispatch=DispatchKind.CONTACT_CENTER,
            required_skill_ids=frozenset({10}),
            published_at=NOW,
        )
        return ServiceFlow(1, 1, "cc", FlowState.ACTIVE, frozenset(), replace(current, **changes))

    with pytest.raises(DomainError):
        check_code_kept(10, "lang_de", None, [flow()])
    with pytest.raises(DomainError):  # the subject's extra skill counts as well
        check_code_kept(
            10,
            "lang_de",
            None,
            [flow(required_skill_ids=frozenset(), subject_skill_ids={"a": frozenset({10})})],
        )
    check_code_kept(10, "lang_de", "other", [flow()])
    check_code_kept(10, "lang_de", None, [flow(dispatch=DispatchKind.OWN_QUEUE)])
    check_code_kept(10, "lang_de", None, [flow(required_skill_ids=frozenset({11}))])
    check_code_kept(10, None, None, [flow()])
