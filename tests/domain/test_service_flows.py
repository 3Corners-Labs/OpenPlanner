"""Service flows, the closed combination table and the readiness checklist (ADR 0121)."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.capacity import CapacityPool
from app.domain.errors import DomainError, ErrorCode
from app.domain.service_flows import (
    BUILT_COMPLETION,
    BUILT_DISPATCH,
    BUILT_KINDS,
    COMBINATIONS,
    RETENTION_BUILT,
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ReadinessCheck,
    ReadinessItem,
    ReadinessPolicy,
    ReadinessReason,
    can_activate,
    legacy_revision,
    readiness,
)
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


def refused_field(make: object) -> str | None:
    """Call ``make`` and return the field of the configuration error it raises."""
    assert callable(make)
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
    assert refused_field(lambda: flow(flow_id=2, current=revision(flow_id=1))) == "flow_id"
    assert flow(flow_id=1, current=revision(flow_id=1)).id == 1
    assert flow(flow_id=None, current=revision(flow_id=1)).id is None
    assert flow(flow_id=1, current=revision(flow_id=None)).id == 1


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
        ({"labels": {"en": "x"}}, R.LABELS_MISSING),
        ({"labels": {"nl": "x"}}, R.LABELS_MISSING),
        ({"labels": {"nl": " ", "en": "x"}}, R.LABELS_MISSING),
        ({"labels": {"nl": "x", "en": ""}}, R.LABELS_MISSING),
        ({"labels": {"nl": "x", "en": "y" * 81}}, R.LABELS_MISSING),
        ({"intake_modes": frozenset()}, R.INTAKE_MISSING),
    ],
)
def test_data_reasons(changes: dict[str, object], reason: ReadinessReason) -> None:
    assert reasons(revision(**changes), pool())[ReadinessCheck.DATA] is reason


def test_labels_missing_comes_before_intake_missing() -> None:
    result = reasons(revision(labels={}, intake_modes=frozenset()), pool())
    assert result[ReadinessCheck.DATA] is R.LABELS_MISSING


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
    assert reasons(desk, pool())[dp] is R.LOCATION_MISSING
    assert reasons(replace(desk, location_id=4), pool())[dp] is None
    view = replace(desk, handling_action=HandlingAction.VIEW_REQUEST)
    assert reasons(view, pool())[dp] is None


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
