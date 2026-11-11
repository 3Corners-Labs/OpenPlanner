"""Service flows, the closed combination table and the readiness checklist (ADR 0121)."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import pytest

from app.domain import service_flows
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import DomainError, ErrorCode
from app.domain.service_flows import (
    BUILT_ACTIONS,
    BUILT_COMPLETION,
    BUILT_DISPATCH,
    BUILT_KINDS,
    COMBINATIONS,
    RETENTION_BUILT,
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ReadinessCheck,
    ReadinessItem,
    ReadinessPolicy,
    ReadinessReason,
    ServiceFlow,
    can_activate,
    check_combination,
    check_default_paths,
    check_draft_content,
    check_draft_save,
    check_flow_references,
    check_publishable,
    check_revision_editable,
    check_state_change,
    choosable_actions,
    default_action,
    draws_on_pool,
    flow_actions,
    legacy_revision,
    readiness,
    revisions_in_effect,
    skills_required_on_pool,
)
from app.domain.skills import Skill
from tests.support.domain import NOW
from tests.support.service_flows import LABELS, flow, legacy, pool, revision

ORDER = tuple(ReadinessCheck)
R = ReadinessReason


def reasons(
    rev: FlowRevision, the_pool: CapacityPool | None = None, subjects: tuple[str, ...] = ()
) -> Mapping[ReadinessCheck, ReadinessReason | None]:
    """Return ``{check: reason}`` of the checklist, asserting its order and consistency."""
    items = readiness(rev, the_pool, offered_subjects=subjects)
    assert tuple(i.code for i in items) == ORDER
    assert all(i.ok == (i.reason is None) for i in items)
    return {i.code: i.reason for i in items}


def refused_field(make: Callable[[], object]) -> str | None:
    """Call ``make`` and return the field of the configuration error it raises."""
    with pytest.raises(DomainError) as caught:
        make()
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    return caught.value.field


def test_the_combination_table_is_closed() -> None:
    for kind, (policy, actions) in {
        InteractionKind.VIDEO: (ReadinessPolicy.MEDIA_PRESENCE, {HandlingAction.OPEN_VIDEO}),
        InteractionKind.TELEPHONE: (ReadinessPolicy.AUTOMATIC, {HandlingAction.CALL}),
        InteractionKind.IN_PERSON: (
            ReadinessPolicy.EXPLICIT_CHECK_IN,
            {HandlingAction.VIEW_REQUEST, HandlingAction.ASSIGN_WORKSTATION},
        ),
        InteractionKind.NONE: (ReadinessPolicy.AUTOMATIC, {HandlingAction.VIEW_REQUEST}),
    }.items():
        assert COMBINATIONS[kind].readiness is policy
        assert COMBINATIONS[kind].actions == actions
    assert set(COMBINATIONS) == set(InteractionKind)


def test_what_is_built_is_exactly_what_the_steps_before_5_and_6_allow() -> None:
    assert BUILT_KINDS == {
        InteractionKind.TELEPHONE,
        InteractionKind.IN_PERSON,
        InteractionKind.NONE,
    }
    assert BUILT_DISPATCH == {DispatchKind.OWN_QUEUE}
    assert BUILT_COMPLETION == {CompletionPolicy.OPERATOR}
    assert BUILT_ACTIONS == {
        HandlingAction.OPEN_VIDEO,
        HandlingAction.VIEW_REQUEST,
        HandlingAction.CALL,
    }
    assert RETENTION_BUILT is False


def test_a_flow_code_follows_the_subject_grammar() -> None:
    for bad in ("", "Balie", "-a", "a" * 41):
        assert refused_field(lambda bad=bad: flow(code=bad)) == "flow_code"
    assert flow(code="a" * 40).code == "a" * 40


def test_a_revision_number_starts_at_one() -> None:
    assert refused_field(lambda: revision(revision=0)) == "revision"
    assert refused_field(lambda: revision(revision=-3)) == "revision"
    assert revision(revision=1).revision == 1


def test_a_flow_cannot_be_the_default_of_an_entry_path_it_does_not_offer() -> None:
    assert refused_field(lambda: flow(default_for=frozenset({IntakeMode.ON_DEMAND}))) == (
        "default_for"
    )
    assert flow(default_for=frozenset({IntakeMode.APPOINTMENT})).default_for == {
        IntakeMode.APPOINTMENT
    }
    both = revision(intake_modes=frozenset(IntakeMode))
    assert flow(default_for=frozenset(IntakeMode), current=both).default_for == set(IntakeMode)


def test_the_revision_must_belong_to_the_flow_when_both_are_stored() -> None:
    published = {"published_at": NOW}
    assert refused_field(lambda: flow(flow_id=2, current=revision(flow_id=1, **published))) == (
        "flow_id"
    )
    assert (
        refused_field(
            lambda: flow(flow_id=2, state=FlowState.DRAFT, current=None, draft=revision(flow_id=1))
        )
        == "flow_id"
    )
    assert flow(flow_id=1, current=revision(flow_id=1, **published)).id == 1
    assert flow(flow_id=None, current=revision(flow_id=1)).id is None
    assert flow(flow_id=1, current=revision(flow_id=None, **published)).id == 1


def test_a_flow_has_a_current_revision_a_draft_or_both() -> None:
    def build(**changes: Any) -> ServiceFlow:
        return replace(flow(current=revision()), **changes)

    assert refused_field(lambda: build(current=None, draft=None)) == "revision"
    assert build(current=None, draft=revision(), state=FlowState.DRAFT).current is None
    assert build(draft=revision(revision=2)).draft is not None


def test_an_active_flow_has_a_current_revision() -> None:
    def build(state: FlowState) -> ServiceFlow:
        return flow(state=state, current=None, draft=revision())

    assert refused_field(lambda: build(FlowState.ACTIVE)) == "state"
    assert build(FlowState.DRAFT).state is FlowState.DRAFT
    assert build(FlowState.ARCHIVED).state is FlowState.ARCHIVED


def test_default_for_follows_the_current_revision_else_the_draft() -> None:
    both = revision(intake_modes=frozenset(IntakeMode))
    only = revision(intake_modes=frozenset({IntakeMode.APPOINTMENT}))
    on_demand = frozenset({IntakeMode.ON_DEMAND})
    assert flow(default_for=on_demand, current=both, draft=only).default_for == on_demand
    assert refused_field(lambda: flow(default_for=on_demand, current=only, draft=both)) == (
        "default_for"
    )
    assert (
        flow(state=FlowState.DRAFT, default_for=on_demand, current=None, draft=both).default_for
        == on_demand
    )


def test_a_stored_flow_has_a_published_current_and_an_unpublished_draft() -> None:
    published = revision(flow_id=1, published_at=NOW)
    unpublished = revision(flow_id=1, revision=2)
    assert flow(flow_id=1, current=published, draft=unpublished).draft == unpublished
    assert (
        refused_field(lambda: flow(flow_id=1, current=revision(flow_id=1), draft=unpublished))
        == "published_at"
    )
    assert (
        refused_field(
            lambda: flow(flow_id=1, current=published, draft=replace(unpublished, published_at=NOW))
        )
        == "published_at"
    )
    # An unsaved flow is not checked: its revisions are stamped when they are stored.
    assert flow(flow_id=None, current=revision(published_at=None)).id is None


def test_the_legacy_wrapper_is_ready_everywhere() -> None:
    assert reasons(legacy(), pool()) == dict.fromkeys(ORDER)
    assert can_activate(readiness(legacy(), pool(), offered_subjects=()))


def test_a_new_flow_cannot_be_activated_before_step_5() -> None:
    assert reasons(revision(), pool()) == {
        **dict.fromkeys(ORDER),
        ReadinessCheck.RETENTION: R.NOT_BUILT_YET,
    }
    assert not can_activate(readiness(revision(), pool(), offered_subjects=()))


def test_retention_is_ready_once_it_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.domain.service_flows.RETENTION_BUILT", True)
    assert reasons(revision(), pool()) == dict.fromkeys(ORDER)
    assert can_activate(readiness(revision(), pool(), offered_subjects=()))


def test_the_legacy_factory_builds_revision_one_of_a_video_wrapper() -> None:
    made = legacy_revision(
        labels=LABELS,
        intake_mode=IntakeMode.ON_DEMAND,
        duration_minutes=15,
        capacity_pool_id=9,
        dispatch=DispatchKind.CONTACT_CENTER,
    )
    assert (made.revision, made.legacy, made.flow_id) == (1, True, None)
    assert made.intake_modes == {IntakeMode.ON_DEMAND}
    assert made.interaction_kind is InteractionKind.VIDEO
    assert made.handling_action is HandlingAction.OPEN_VIDEO
    assert made.completion is CompletionPolicy.LEGACY_VIDEO_OBSERVATION
    assert made.dispatch is DispatchKind.CONTACT_CENTER
    assert (made.duration_minutes, made.capacity_pool_id) == (15, 9)
    assert made.required_skill_ids == frozenset()
    assert made.subject_skill_ids == {}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"labels": {"en": "x"}}, R.LABELS_INVALID),
        ({"labels": {"nl": "x"}}, R.LABELS_INVALID),
        ({"labels": {"nl": " ", "en": "x"}}, R.LABELS_INVALID),
        ({"labels": {"nl": "x", "en": ""}}, R.LABELS_INVALID),
        ({"labels": {"nl": "x", "en": "y" * 81}}, R.LABELS_INVALID),
        ({"intake_modes": frozenset()}, R.INTAKE_MISSING),
    ],
)
def test_data_reasons(changes: dict[str, object], reason: ReadinessReason) -> None:
    assert reasons(revision(**changes), pool())[ReadinessCheck.DATA] is reason


def test_invalid_labels_come_before_a_missing_intake() -> None:
    result = reasons(revision(labels={}, intake_modes=frozenset()), pool())
    assert result[ReadinessCheck.DATA] is R.LABELS_INVALID


def test_capacity_reasons() -> None:
    cap = ReadinessCheck.CAPACITY
    assert reasons(revision(capacity_pool_id=None), pool())[cap] is R.POOL_MISSING
    assert reasons(revision(), None)[cap] is R.POOL_MISSING
    archived = pool(archived_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert reasons(revision(), archived)[cap] is R.POOL_ARCHIVED
    assert reasons(revision(duration_minutes=None), pool())[cap] is R.DURATION


@pytest.mark.parametrize(
    ("minutes", "ok"),
    [(4, False), (5, True), (7, False), (20, True), (480, True), (485, False), (0, False)],
)
def test_duration_follows_the_slot_length_rule(minutes: int, ok: bool) -> None:
    result = reasons(revision(duration_minutes=minutes), pool())[ReadinessCheck.CAPACITY]
    assert result is (None if ok else R.DURATION)


def test_skills_reasons() -> None:
    sk = ReadinessCheck.SKILLS
    assert reasons(revision(), None)[sk] is R.POOL_MISSING
    assert reasons(revision(capacity_pool_id=None), pool())[sk] is R.POOL_MISSING
    needs = revision(required_skill_ids=frozenset({1, 2}))
    assert reasons(needs, pool(guaranteed=frozenset({1})))[sk] is R.POOL_DOES_NOT_GUARANTEE
    assert reasons(needs, pool(guaranteed=frozenset({1, 2, 3})))[sk] is None
    extra = revision(subject_skill_ids={"id": frozenset({5})})
    assert reasons(extra, pool(), ("id",))[sk] is R.POOL_DOES_NOT_GUARANTEE
    assert reasons(extra, pool(guaranteed=frozenset({5})), ("id",))[sk] is None
    assert reasons(extra, pool(guaranteed=frozenset({5})), ("other",))[sk] is R.SUBJECT_UNKNOWN
    assert reasons(extra, pool(guaranteed=frozenset({5})))[sk] is R.SUBJECT_UNKNOWN


def test_subject_requirements_follow_each_flows_own_extra_skills_a06() -> None:
    sk = ReadinessCheck.SKILLS
    shared = ("passport",)
    flow_a = revision(subject_skill_ids={"passport": frozenset({1})})
    flow_b = revision(subject_skill_ids={"passport": frozenset({2})})
    assert reasons(flow_a, pool(guaranteed=frozenset({1})), shared)[sk] is None
    assert reasons(flow_b, pool(guaranteed=frozenset({1})), shared)[sk] is R.POOL_DOES_NOT_GUARANTEE
    assert reasons(flow_b, pool(guaranteed=frozenset({2})), shared)[sk] is None
    assert reasons(flow_a, pool(guaranteed=frozenset({2})), shared)[sk] is R.POOL_DOES_NOT_GUARANTEE


def test_a13_a_pool_guaranteeing_only_x_does_not_cover_x_and_y() -> None:
    both = revision(required_skill_ids=frozenset({1, 2}))
    result = reasons(both, pool(guaranteed=frozenset({1})))[ReadinessCheck.SKILLS]
    assert result is R.POOL_DOES_NOT_GUARANTEE


def test_dispatch_reasons() -> None:
    dp = ReadinessCheck.DISPATCH
    assert reasons(revision(interaction_kind=None), pool())[dp] is R.KIND_MISSING
    assert reasons(revision(dispatch=None), pool())[dp] is R.DISPATCH_MISSING
    assert reasons(revision(handling_action=None), pool())[dp] is R.ACTION_NOT_ALLOWED
    wrong = revision(handling_action=HandlingAction.VIEW_REQUEST)
    assert reasons(wrong, pool())[dp] is R.ACTION_NOT_ALLOWED
    assert reasons(legacy(handling_action=HandlingAction.CALL), pool())[dp] is R.ACTION_NOT_ALLOWED


def test_a_workstation_needs_a_location() -> None:
    dp = ReadinessCheck.DISPATCH
    desk = revision(
        interaction_kind=InteractionKind.IN_PERSON,
        handling_action=HandlingAction.ASSIGN_WORKSTATION,
    )
    old = replace(desk, legacy=True)  # a legacy revision skips the "built" tests
    assert reasons(old, pool())[dp] is R.LOCATION_MISSING
    assert reasons(replace(old, location_id=4), pool())[dp] is None
    view = replace(desk, handling_action=HandlingAction.VIEW_REQUEST)
    assert reasons(view, pool())[dp] is None


def test_assigning_a_workstation_is_not_built_yet() -> None:
    desk = revision(
        interaction_kind=InteractionKind.IN_PERSON,
        handling_action=HandlingAction.ASSIGN_WORKSTATION,
    )
    assert reasons(desk, pool())[ReadinessCheck.DISPATCH] is R.NOT_BUILT_YET
    assert reasons(replace(desk, location_id=4), pool())[ReadinessCheck.DISPATCH] is (
        R.NOT_BUILT_YET
    )


def test_video_and_contact_center_wait_for_their_steps_unless_legacy() -> None:
    dp = ReadinessCheck.DISPATCH
    video = revision(
        interaction_kind=InteractionKind.VIDEO, handling_action=HandlingAction.OPEN_VIDEO
    )
    assert reasons(video, pool())[dp] is R.NOT_BUILT_YET
    cc = revision(dispatch=DispatchKind.CONTACT_CENTER)
    assert reasons(cc, pool())[dp] is R.NOT_BUILT_YET
    assert reasons(legacy(dispatch=DispatchKind.CONTACT_CENTER), pool())[dp] is None
    assert reasons(legacy(), pool())[dp] is None


def test_completion_reasons() -> None:
    co = ReadinessCheck.COMPLETION
    assert reasons(revision(completion=None), pool())[co] is R.COMPLETION_MISSING
    old = revision(completion=CompletionPolicy.LEGACY_VIDEO_OBSERVATION)
    assert reasons(old, pool())[co] is R.COMPLETION_LEGACY_ONLY
    event = revision(completion=CompletionPolicy.PROVIDER_EVENT)
    assert reasons(event, pool())[co] is R.NOT_BUILT_YET
    assert reasons(legacy(), pool())[co] is None
    assert reasons(legacy(completion=CompletionPolicy.PROVIDER_EVENT), pool())[co] is None
    assert reasons(legacy(completion=None), pool())[co] is R.COMPLETION_MISSING


def test_can_activate_needs_every_item() -> None:
    a, b = ReadinessCheck.DATA, ReadinessCheck.CAPACITY
    assert can_activate([ReadinessItem(a, True), ReadinessItem(b, True)])
    assert not can_activate([ReadinessItem(a, True), ReadinessItem(b, False, R.DURATION)])
    assert can_activate([])


def test_no_behaviour_depends_on_a_label() -> None:
    plain = revision()
    odd = revision(labels={"nl": "zzz", "en": "Balie 7"})
    assert reasons(plain, pool()) == reasons(odd, pool())


# --- the rules the storage adapters and the fake share ----------------------------------------

ORGANISATION = 1


def refusal(call: Callable[[], object]) -> tuple[ErrorCode, str | None]:
    """Call ``call`` and return the code and field of the domain error it raises."""
    with pytest.raises(DomainError) as caught:
        call()
    return caught.value.code, caught.value.field


def references(
    changes: Mapping[str, Any] | None = None,
    *,
    the_pool: CapacityPool | None = None,
    skills: Mapping[int, Skill] | None = None,
    already: frozenset[int] = frozenset(),
    subjects: tuple[str, ...] = (),
    already_pool_id: int | None = None,
) -> None:
    """Run ``check_flow_references`` for Acme's group (organisation 1)."""
    check_flow_references(
        ORGANISATION,
        revision(**(changes or {})),
        the_pool,
        skills or {},
        already,
        subjects,
        group_id=1,
        already_pool_id=already_pool_id,
    )


def stored_skill(skill_id: int, organisation: int = 1, archived: bool = False) -> Skill:
    """Return a loaded skill."""
    return Skill(
        skill_id,
        organisation,
        f"s{skill_id}",
        {"nl": "S", "en": "S"},
        NOW if archived else None,
    )


def test_a_revision_without_references_passes_the_reference_check() -> None:
    references({"capacity_pool_id": None})


def test_the_pool_must_exist_and_belong_to_the_organisation() -> None:
    assert refusal(lambda: references(the_pool=None)) == (
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "capacity_pool_id",
    )
    other = replace(pool(), organisation_id=2)
    assert refusal(lambda: references(the_pool=other)) == (
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        "capacity_pool_id",
    )
    references(the_pool=pool())


def test_an_archived_pool_may_stay_but_is_not_newly_chosen() -> None:
    archived = pool(archived_at=NOW)
    assert refusal(lambda: references(the_pool=archived)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "capacity_pool_id",
    )
    assert refusal(lambda: references(the_pool=archived, already_pool_id=2)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "capacity_pool_id",
    )
    references(the_pool=archived, already_pool_id=1)


def test_every_subject_key_is_one_of_the_groups_subjects() -> None:
    extra = {"subject_skill_ids": {"id": frozenset()}}
    assert refusal(lambda: references(extra, the_pool=pool(), subjects=("visa",))) == (
        ErrorCode.INVALID_CONFIGURATION,
        "subject_skill_ids",
    )
    references(extra, the_pool=pool(), subjects=("id", "visa"))


def test_the_skills_of_a_revision_follow_the_catalogue_rules() -> None:
    needs = {"required_skill_ids": frozenset({1}), "subject_skill_ids": {"id": frozenset({2})}}

    def check(skills: dict[int, Skill], already: frozenset[int] = frozenset()) -> None:
        references(needs, the_pool=pool(), skills=skills, already=already, subjects=("id",))

    assert refusal(lambda: check({1: stored_skill(1)})) == (
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "required_skill_ids",
    )
    assert refusal(lambda: check({1: stored_skill(1), 2: stored_skill(2, 2)})) == (
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        "required_skill_ids",
    )
    old = {1: stored_skill(1), 2: stored_skill(2, archived=True)}
    assert refusal(lambda: check(old)) == (ErrorCode.INVALID_CONFIGURATION, "required_skill_ids")
    check(old, frozenset({2}))


def test_a_revision_names_every_skill_of_the_flow_and_its_subjects() -> None:
    assert revision().all_skill_ids == frozenset()
    named = revision(
        required_skill_ids=frozenset({1}),
        subject_skill_ids={"id": frozenset({2, 3}), "visa": frozenset(), "x": frozenset({1})},
    )
    assert named.all_skill_ids == frozenset({1, 2, 3})


def test_a_revision_names_no_location_until_locations_are_built() -> None:
    assert refusal(lambda: references({"location_id": 7}, the_pool=pool())) == (
        ErrorCode.INVALID_CONFIGURATION,
        "location_id",
    )


def test_a_default_entry_path_is_taken_by_one_active_flow() -> None:
    mine = flow(default_for=frozenset({IntakeMode.APPOINTMENT}))
    assert refusal(lambda: check_default_paths(mine, [IntakeMode.APPOINTMENT])) == (
        ErrorCode.CONFIGURATION_OBJECT_EXISTS,
        "default_for",
    )
    check_default_paths(mine, [IntakeMode.ON_DEMAND])
    check_default_paths(mine, [])
    check_default_paths(flow(), [IntakeMode.APPOINTMENT])


@pytest.mark.parametrize(
    ("current", "new", "allowed"),
    [
        (FlowState.DRAFT, FlowState.ACTIVE, True),
        (FlowState.ACTIVE, FlowState.ARCHIVED, True),
        (FlowState.ARCHIVED, FlowState.ACTIVE, True),
        (FlowState.DRAFT, FlowState.ARCHIVED, True),
        (FlowState.ACTIVE, FlowState.DRAFT, False),
        (FlowState.ARCHIVED, FlowState.DRAFT, False),
        (FlowState.ACTIVE, FlowState.ACTIVE, False),
        (FlowState.DRAFT, FlowState.DRAFT, False),
    ],
)
def test_the_lifecycle_has_three_changes(current: FlowState, new: FlowState, allowed: bool) -> None:
    flow_in_state = flow(state=current, current=revision(published_at=NOW))
    if allowed:
        check_state_change(flow_in_state, new)
    else:
        assert refusal(lambda: check_state_change(flow_in_state, new)) == (
            ErrorCode.INVALID_CONFIGURATION,
            "state",
        )


def test_a_flow_that_was_never_published_is_not_archived() -> None:
    unpublished = flow(state=FlowState.DRAFT, current=None, draft=revision())

    assert refusal(lambda: check_state_change(unpublished, FlowState.ARCHIVED)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "state",
    )


def test_activating_needs_a_published_revision() -> None:
    unpublished = flow(state=FlowState.DRAFT, current=None, draft=revision())
    assert refusal(lambda: check_state_change(unpublished, FlowState.ACTIVE)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "state",
    )
    archived_old = flow(state=FlowState.ARCHIVED, current=legacy(published_at=NOW))
    check_state_change(archived_old, FlowState.ACTIVE)
    check_state_change(replace(archived_old, state=FlowState.ACTIVE), FlowState.ARCHIVED)


def test_publishing_needs_a_draft_that_keeps_the_default_paths() -> None:
    assert refusal(lambda: check_publishable(flow())) == (
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "draft",
    )
    both = revision(intake_modes=frozenset(IntakeMode))
    mine = frozenset({IntakeMode.ON_DEMAND})
    shrunk = revision(revision=2)  # appointment only
    assert refusal(
        lambda: check_publishable(flow(default_for=mine, current=both, draft=shrunk))
    ) == (
        ErrorCode.INVALID_CONFIGURATION,
        "default_for",
    )
    check_publishable(flow(default_for=mine, current=both, draft=replace(both, revision=2)))


def draft_flow(**changes: Any) -> ServiceFlow:
    """Return a new unsaved flow with a draft; ``changes`` override the flow's fields."""
    return replace(flow(state=FlowState.DRAFT, current=None, draft=revision()), **changes)


def stored_flow(**changes: Any) -> ServiceFlow:
    """Return a stored flow with a published current revision (and no draft)."""
    return replace(
        flow(state=FlowState.ACTIVE, flow_id=1, current=revision(flow_id=1, published_at=NOW)),
        **changes,
    )


def test_a_new_flow_is_a_draft_without_a_current_revision() -> None:
    assert check_draft_save(draft_flow(), None, False) == revision()
    assert refusal(lambda: check_draft_save(stored_flow(), stored_flow(), False)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "draft",
    )
    published = revision(published_at=NOW)
    assert refusal(lambda: check_draft_save(draft_flow(draft=published), None, False)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "published_at",
    )
    assert refusal(lambda: check_draft_save(draft_flow(draft=legacy()), None, False)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "legacy",
    )
    assert refusal(lambda: check_draft_save(draft_flow(state=FlowState.ARCHIVED), None, False)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "state",
    )
    assert refusal(lambda: check_draft_save(draft_flow(current=revision()), None, False)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "revision",
    )


def test_a_code_taken_in_the_group_is_refused() -> None:
    assert refusal(lambda: check_draft_save(draft_flow(), None, True)) == (
        ErrorCode.SLUG_TAKEN,
        "flow_code",
    )


def test_an_existing_flow_must_be_found_in_the_callers_scope_and_group() -> None:
    mine = replace(stored_flow(), draft=revision(flow_id=1, revision=2))
    assert refusal(lambda: check_draft_save(mine, None, False)) == (
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "flow_id",
    )
    elsewhere = replace(stored_flow(), group_id=2)
    assert refusal(lambda: check_draft_save(mine, elsewhere, False)) == (
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        "group_id",
    )
    assert check_draft_save(mine, stored_flow(), False).revision == 2


def test_a_published_flow_keeps_its_code_and_defaults_a_never_published_one_may_change() -> None:
    mine = replace(stored_flow(), draft=revision(flow_id=1, revision=2))
    assert refusal(lambda: check_draft_save(replace(mine, code="b"), stored_flow(), False)) == (
        ErrorCode.INVALID_CONFIGURATION,
        "code",
    )
    defaults = frozenset({IntakeMode.APPOINTMENT})
    assert refusal(
        lambda: check_draft_save(replace(mine, default_for=defaults), stored_flow(), False)
    ) == (ErrorCode.INVALID_CONFIGURATION, "default_for")
    never = replace(
        flow(state=FlowState.DRAFT, flow_id=1, current=None, draft=revision(flow_id=1)),
    )
    check_draft_save(replace(never, code="b", default_for=defaults), never, False)


# --- what is in effect, what a flow draws on, history ---------------------------------------


def test_the_revisions_in_effect_are_the_current_and_the_draft_only() -> None:
    both = flow(
        flow_id=1,
        current=revision(revision=2, published_at=NOW),
        draft=revision(revision=3, published_at=None),
    )

    assert [r.revision for r in revisions_in_effect(both)] == [2, 3]
    only_draft = flow(state=FlowState.DRAFT, draft=revision())
    assert revisions_in_effect(only_draft) == (revision(),)


def test_a_flow_draws_on_a_pool_by_a_revision_in_effect_unless_it_is_archived() -> None:
    drawing = flow(flow_id=1, current=revision(capacity_pool_id=5, published_at=NOW))
    drafting = flow(draft=revision(capacity_pool_id=5), state=FlowState.DRAFT)

    assert draws_on_pool(drawing, 5)
    assert draws_on_pool(drafting, 5)
    assert not draws_on_pool(drawing, 6)
    assert not draws_on_pool(replace(drawing, state=FlowState.ARCHIVED), 5)


def test_the_skills_flows_require_on_a_pool_are_those_of_the_flow_and_its_subjects() -> None:
    first = flow(
        "a",
        current=revision(
            capacity_pool_id=5,
            required_skill_ids=frozenset({1}),
            subject_skill_ids={"id": frozenset({2})},
            published_at=NOW,
        ),
        flow_id=1,
    )
    elsewhere = flow("b", current=revision(capacity_pool_id=6, required_skill_ids=frozenset({3})))
    archived = replace(
        flow("c", current=revision(capacity_pool_id=5, required_skill_ids=frozenset({4}))),
        state=FlowState.ARCHIVED,
    )

    assert skills_required_on_pool([first, elsewhere, archived], 5) == {1, 2}
    assert skills_required_on_pool([], 5) == frozenset()


def test_only_a_revision_that_is_not_published_may_be_changed() -> None:
    check_revision_editable(None)
    assert refusal(lambda: check_revision_editable(NOW, "labels")) == (
        ErrorCode.CONFIGURATION_REVISION_PUBLISHED,
        "labels",
    )
    assert refusal(lambda: check_revision_editable(NOW)) == (
        ErrorCode.CONFIGURATION_REVISION_PUBLISHED,
        "revision",
    )


def test_an_action_must_be_one_the_interaction_kind_allows_and_open_choices_pass() -> None:
    check_combination(None, HandlingAction.CALL)
    check_combination(InteractionKind.TELEPHONE, None)
    check_combination(InteractionKind.TELEPHONE, HandlingAction.CALL)
    assert refusal(
        lambda: check_combination(InteractionKind.TELEPHONE, HandlingAction.OPEN_VIDEO)
    ) == (
        ErrorCode.INVALID_CONFIGURATION,
        "handling_action",
    )


def test_a_legacy_pool_serves_the_legacy_wrapper_of_its_own_group_only() -> None:
    wrapper = replace(pool(mode=PoolMode.LEGACY_GROUP), group_id=1)

    check_flow_references(
        1, legacy(), wrapper, {}, frozenset(), (), group_id=1, already_pool_id=None
    )
    assert refusal(
        lambda: check_flow_references(
            1, legacy(), wrapper, {}, frozenset(), (), group_id=2, already_pool_id=None
        )
    ) == (ErrorCode.INVALID_CONFIGURATION, "capacity_pool_id")


@pytest.mark.parametrize("own_group", [True, False])
def test_a_new_revision_never_draws_on_a_legacy_pool_not_its_groups_own_either(
    own_group: bool,
) -> None:
    wrapper = replace(pool(mode=PoolMode.LEGACY_GROUP), group_id=1)

    assert refusal(
        lambda: check_flow_references(
            1,
            revision(),
            wrapper,
            {},
            frozenset(),
            (),
            group_id=1 if own_group else 2,
            already_pool_id=None,
        )
    ) == (ErrorCode.INVALID_CONFIGURATION, "capacity_pool_id")
    check_flow_references(
        1, revision(), pool(), {}, frozenset(), (), group_id=1, already_pool_id=None
    )


def test_a_flow_knows_whether_it_is_legacy_and_which_revision_a_list_and_a_page_show() -> None:
    wrapper = flow(current=legacy(published_at=NOW), flow_id=1)
    own = flow(flow_id=1, current=revision(published_at=NOW), draft=revision(revision=2))
    fresh = flow(state=FlowState.DRAFT, current=None, draft=revision())

    assert (wrapper.legacy, own.legacy, fresh.legacy) == (True, False, False)
    assert own.listed is own.current
    assert own.working is own.draft
    assert fresh.listed is fresh.working is fresh.draft
    assert flow(current=revision(published_at=NOW), flow_id=1).working.revision == 1


def test_the_actions_of_a_channel_follow_the_closed_table() -> None:
    assert choosable_actions(None) == ()
    assert choosable_actions(InteractionKind.IN_PERSON) == (
        HandlingAction.VIEW_REQUEST,
        HandlingAction.ASSIGN_WORKSTATION,
    )
    assert choosable_actions(InteractionKind.TELEPHONE) == (HandlingAction.CALL,)
    assert default_action(None) is None
    assert default_action(InteractionKind.IN_PERSON) is HandlingAction.VIEW_REQUEST
    assert default_action(InteractionKind.VIDEO) is HandlingAction.OPEN_VIDEO


def test_the_default_action_is_the_first_one_that_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_flows, "BUILT_ACTIONS", frozenset({HandlingAction.ASSIGN_WORKSTATION})
    )

    assert default_action(InteractionKind.IN_PERSON) is HandlingAction.ASSIGN_WORKSTATION
    monkeypatch.setattr(service_flows, "BUILT_ACTIONS", frozenset())
    assert default_action(InteractionKind.IN_PERSON) is HandlingAction.VIEW_REQUEST


def ready_items(ok: bool = True) -> tuple[ReadinessItem, ...]:
    """Return a checklist that is in order, or has one point missing."""
    return tuple(
        ReadinessItem(
            check, ok or check is not ReadinessCheck.DATA, None if ok else R.LABELS_INVALID
        )
        for check in ReadinessCheck
    )


@pytest.mark.parametrize(
    ("the_flow", "items", "expected"),
    [
        (flow(state=FlowState.DRAFT, current=None, draft=revision()), None, "save publish discard"),
        (
            flow(state=FlowState.DRAFT, flow_id=1),
            ready_items(),
            "prepare activate archive",
        ),
        (flow(state=FlowState.DRAFT, flow_id=1), ready_items(False), "prepare archive"),
        (
            flow(
                state=FlowState.DRAFT,
                flow_id=1,
                current=revision(published_at=NOW),
                draft=revision(revision=2),
            ),
            ready_items(),
            "save publish discard activate archive",
        ),
        (flow(state=FlowState.ACTIVE, flow_id=1), ready_items(), "prepare archive"),
        (
            flow(
                state=FlowState.ACTIVE,
                flow_id=1,
                current=revision(published_at=NOW),
                draft=revision(revision=2),
            ),
            ready_items(),
            "save publish discard archive",
        ),
        (flow(state=FlowState.ARCHIVED, flow_id=1), ready_items(), ""),
        (
            flow(state=FlowState.ACTIVE, flow_id=1, current=legacy(published_at=NOW)),
            ready_items(),
            "",
        ),
    ],
    ids=[
        "never published",
        "published, ready",
        "published, not ready",
        "published with a draft",
        "active",
        "active with a draft",
        "archived",
        "legacy",
    ],
)
def test_the_actions_the_lifecycle_allows(
    the_flow: ServiceFlow, items: tuple[ReadinessItem, ...] | None, expected: str
) -> None:
    allowed = flow_actions(the_flow, items)

    assert {name for name, on in vars(allowed).items() if on} == set(expected.split())


def test_activation_needs_the_checklist_of_the_published_revision_in_order() -> None:
    published = flow(state=FlowState.DRAFT, flow_id=1)

    assert flow_actions(published, None).activate is False
    assert flow_actions(published, ready_items(False)).activate is False
    assert flow_actions(published, ready_items()).activate is True


def test_a_draft_that_is_wrong_in_itself_is_refused_and_one_that_is_incomplete_passes() -> None:
    check_draft_content(
        revision(
            interaction_kind=None,
            duration_minutes=None,
            dispatch=None,
            completion=None,
            handling_action=None,
        )
    )
    wrong = {
        "labels": {"labels": {"nl": "x", "en": ""}},
        "duration_minutes": {"duration_minutes": 7},
        "interaction_kind": {
            "interaction_kind": InteractionKind.VIDEO,
            "handling_action": HandlingAction.OPEN_VIDEO,
        },
        "dispatch": {"dispatch": DispatchKind.CONTACT_CENTER},
        "completion": {"completion": CompletionPolicy.PROVIDER_EVENT},
        "handling_action": {
            "interaction_kind": InteractionKind.IN_PERSON,
            "handling_action": HandlingAction.ASSIGN_WORKSTATION,
        },
    }
    for field, changes in wrong.items():
        assert refusal(lambda c=changes: check_draft_content(revision(**c))) == (
            ErrorCode.INVALID_CONFIGURATION,
            field,
        )
    assert refusal(
        lambda: check_draft_content(
            revision(interaction_kind=None, handling_action=HandlingAction.CALL)
        )
    ) == (ErrorCode.INVALID_CONFIGURATION, "handling_action")
    assert refusal(
        lambda: check_draft_content(
            revision(interaction_kind=InteractionKind.NONE, handling_action=HandlingAction.CALL)
        )
    ) == (ErrorCode.INVALID_CONFIGURATION, "handling_action")


@pytest.mark.parametrize(
    ("constant", "member", "changes"),
    [
        (
            "BUILT_KINDS",
            InteractionKind.VIDEO,
            {
                "interaction_kind": InteractionKind.VIDEO,
                "handling_action": HandlingAction.OPEN_VIDEO,
            },
        ),
        ("BUILT_DISPATCH", DispatchKind.CONTACT_CENTER, {"dispatch": DispatchKind.CONTACT_CENTER}),
        (
            "BUILT_COMPLETION",
            CompletionPolicy.PROVIDER_EVENT,
            {"completion": CompletionPolicy.PROVIDER_EVENT},
        ),
        (
            "BUILT_ACTIONS",
            HandlingAction.ASSIGN_WORKSTATION,
            {
                "interaction_kind": InteractionKind.IN_PERSON,
                "handling_action": HandlingAction.ASSIGN_WORKSTATION,
            },
        ),
    ],
)
def test_each_built_constant_alone_decides_what_a_draft_may_choose(
    monkeypatch: pytest.MonkeyPatch, constant: str, member: StrEnum, changes: dict[str, Any]
) -> None:
    assert refusal(lambda: check_draft_content(revision(**changes)))[0] is (
        ErrorCode.INVALID_CONFIGURATION
    )

    monkeypatch.setattr(
        service_flows, constant, frozenset({*getattr(service_flows, constant), member})
    )

    check_draft_content(revision(**changes))


def test_a_legacy_draft_is_not_held_to_what_is_built() -> None:
    check_draft_content(legacy(published_at=None))
