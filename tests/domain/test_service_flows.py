"""Service flows, the closed combination table and the readiness checklist (ADR 0121)."""

from dataclasses import replace

import pytest

from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import DomainError, ErrorCode
from app.domain.service_flows import (
    COMBINATIONS,
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ReadinessItem,
    ReadinessPolicy,
    ServiceFlow,
    can_activate,
    legacy_revision,
    next_revision,
    readiness,
    readiness_policy,
    resolve_default,
    resolve_explicit,
)

ORDER = ("data", "capacity", "skills", "dispatch", "completion", "retention")
LABELS = {"nl": "Balie", "en": "Desk"}


def pool(*skills: int, **kw: object) -> CapacityPool:
    """Return a counted pool guaranteeing ``skills``."""
    return CapacityPool(1, 1, "Loket", PoolMode.COUNTED, frozenset(skills), **kw)  # type: ignore[arg-type]


def revision(**changes: object) -> FlowRevision:
    """Return a complete, non-legacy telephone revision; ``changes`` override fields."""
    base = FlowRevision(
        flow_id=1,
        revision=1,
        labels=LABELS,
        intake_modes=frozenset({IntakeMode.APPOINTMENT}),
        interaction_kind=InteractionKind.TELEPHONE,
        duration_minutes=20,
        capacity_pool_id=1,
        required_skill_ids=frozenset(),
        subject_skill_ids={},
        dispatch=DispatchKind.OWN_QUEUE,
        handling_action=HandlingAction.CALL,
        completion=CompletionPolicy.OPERATOR,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def legacy(**changes: object) -> FlowRevision:
    """Return a legacy revision."""
    base = legacy_revision(
        labels=LABELS,
        intake_mode=IntakeMode.APPOINTMENT,
        duration_minutes=20,
        capacity_pool_id=1,
        dispatch=DispatchKind.OWN_QUEUE,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def reasons(
    rev: FlowRevision, the_pool: CapacityPool | None = None, subjects: tuple[str, ...] = ()
):  # noqa: ANN201
    """Return ``{item code: reason}`` of the checklist."""
    items = readiness(rev, the_pool, offered_subjects=subjects)
    assert tuple(i.code for i in items) == ORDER
    assert all(i.ok == (i.reason is None) for i in items)
    return {i.code: i.reason for i in items}


def flow(code: str = "a", state: FlowState = FlowState.ACTIVE, **kw: object) -> ServiceFlow:
    """Return a flow of group 1."""
    values: dict[str, object] = {
        "default_for": frozenset(),
        "current": revision(),
    }
    values.update(kw)
    return ServiceFlow(None, 1, code, state, **values)  # type: ignore[arg-type]


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
        assert readiness_policy(kind) is policy
    assert set(COMBINATIONS) == set(InteractionKind)


def test_a_flow_code_follows_the_subject_grammar() -> None:
    for bad in ("", "Balie", "-a", "a" * 41):
        with pytest.raises(DomainError) as caught:
            flow(code=bad)
        assert (caught.value.code, caught.value.field) == (
            ErrorCode.INVALID_CONFIGURATION,
            "flow_code",
        )
    assert flow(code="a" * 40).code == "a" * 40


def test_the_legacy_wrapper_is_ready_everywhere() -> None:
    assert reasons(legacy(), pool()) == dict.fromkeys(ORDER)
    assert can_activate(readiness(legacy(), pool(), offered_subjects=()))


def test_a_new_flow_cannot_be_activated_before_step_5() -> None:
    result = reasons(revision(), pool())
    assert result == {**dict.fromkeys(ORDER), "retention": "not_built_yet"}
    assert not can_activate(readiness(revision(), pool(), offered_subjects=()))


def test_the_legacy_factory_builds_revision_one() -> None:
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
    assert made.collects_email is True


def test_next_revision_copies_and_never_mutates() -> None:
    first = revision()
    second = next_revision(first, duration_minutes=30)
    assert (first.revision, first.duration_minutes) == (1, 20)
    assert (second.revision, second.duration_minutes) == (2, 30)
    assert next_revision(second).revision == 3


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"labels": {"en": "x"}}, "labels_missing"),
        ({"labels": {"nl": "x"}}, "labels_missing"),
        ({"labels": {"nl": " ", "en": "x"}}, "labels_missing"),
        ({"labels": {"nl": "x", "en": ""}}, "labels_missing"),
        ({"intake_modes": frozenset()}, "intake_missing"),
    ],
)
def test_data_reasons(changes: dict[str, object], reason: str) -> None:
    assert reasons(revision(**changes), pool())["data"] == reason


def test_labels_missing_comes_before_intake_missing() -> None:
    assert reasons(revision(labels={}, intake_modes=frozenset()), pool())["data"] == (
        "labels_missing"
    )


def test_capacity_reasons() -> None:
    assert reasons(revision(capacity_pool_id=None), pool())["capacity"] == "pool_missing"
    assert reasons(revision(), None)["capacity"] == "pool_missing"
    from datetime import UTC, datetime

    archived = pool(archived_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert reasons(revision(), archived)["capacity"] == "pool_archived"
    assert reasons(revision(duration_minutes=None), pool())["capacity"] == "duration"


@pytest.mark.parametrize(("minutes", "ok"), [(4, False), (5, True), (480, True), (481, False)])
def test_duration_boundaries(minutes: int, ok: bool) -> None:
    result = reasons(revision(duration_minutes=minutes), pool())["capacity"]
    assert result == (None if ok else "duration")


def test_skills_reasons() -> None:
    assert reasons(revision(), None)["skills"] == "pool_missing"
    assert reasons(revision(capacity_pool_id=None), pool())["skills"] == "pool_missing"
    needs = revision(required_skill_ids=frozenset({1, 2}))
    assert reasons(needs, pool(1))["skills"] == "pool_does_not_guarantee"
    assert reasons(needs, pool(1, 2, 3))["skills"] is None
    extra = revision(subject_skill_ids={"id": frozenset({5})})
    assert reasons(extra, pool(), ("id",))["skills"] == "pool_does_not_guarantee"
    assert reasons(extra, pool(5), ("id",))["skills"] is None
    assert reasons(extra, pool(5), ("other",))["skills"] == "subject_unknown"
    assert reasons(extra, pool(5))["skills"] == "subject_unknown"


def test_subject_requirements_follow_each_flows_own_extra_skills_a06() -> None:
    shared = ("passport",)
    flow_a = revision(subject_skill_ids={"passport": frozenset({1})})
    flow_b = revision(subject_skill_ids={"passport": frozenset({2})})
    assert reasons(flow_a, pool(1), shared)["skills"] is None
    assert reasons(flow_b, pool(1), shared)["skills"] == "pool_does_not_guarantee"
    assert reasons(flow_b, pool(2), shared)["skills"] is None
    assert reasons(flow_a, pool(2), shared)["skills"] == "pool_does_not_guarantee"


def test_a13_a_pool_guaranteeing_only_x_does_not_cover_x_and_y() -> None:
    both = revision(required_skill_ids=frozenset({1, 2}))
    assert reasons(both, pool(1))["skills"] == "pool_does_not_guarantee"


def test_dispatch_reasons() -> None:
    assert reasons(revision(interaction_kind=None), pool())["dispatch"] == "kind_missing"
    assert reasons(revision(dispatch=None), pool())["dispatch"] == "dispatch_missing"
    assert reasons(revision(handling_action=None), pool())["dispatch"] == "action_not_allowed"
    wrong = revision(handling_action=HandlingAction.VIEW_REQUEST)
    assert reasons(wrong, pool())["dispatch"] == "action_not_allowed"
    assert reasons(legacy(handling_action=HandlingAction.CALL), pool())["dispatch"] == (
        "action_not_allowed"
    )


def test_a_workstation_needs_a_location() -> None:
    desk = revision(
        interaction_kind=InteractionKind.IN_PERSON,
        handling_action=HandlingAction.ASSIGN_WORKSTATION,
    )
    assert reasons(desk, pool())["dispatch"] == "location_missing"
    assert reasons(replace(desk, location_id=4), pool())["dispatch"] is None
    view = replace(desk, handling_action=HandlingAction.VIEW_REQUEST)
    assert reasons(view, pool())["dispatch"] is None


def test_video_and_contact_center_wait_for_their_steps_unless_legacy() -> None:
    video = revision(
        interaction_kind=InteractionKind.VIDEO, handling_action=HandlingAction.OPEN_VIDEO
    )
    assert reasons(video, pool())["dispatch"] == "not_built_yet"
    cc = revision(dispatch=DispatchKind.CONTACT_CENTER)
    assert reasons(cc, pool())["dispatch"] == "not_built_yet"
    assert reasons(legacy(dispatch=DispatchKind.CONTACT_CENTER), pool())["dispatch"] is None
    assert reasons(legacy(), pool())["dispatch"] is None


def test_completion_reasons() -> None:
    assert reasons(revision(completion=None), pool())["completion"] == "completion_missing"
    old = revision(completion=CompletionPolicy.LEGACY_VIDEO_OBSERVATION)
    assert reasons(old, pool())["completion"] == "completion_legacy_only"
    event = revision(completion=CompletionPolicy.PROVIDER_EVENT)
    assert reasons(event, pool())["completion"] == "not_built_yet"
    assert reasons(legacy(), pool())["completion"] is None
    assert reasons(legacy(completion=CompletionPolicy.PROVIDER_EVENT), pool())["completion"] is None
    assert reasons(legacy(completion=None), pool())["completion"] == "completion_missing"


def test_can_activate_needs_every_item() -> None:
    assert can_activate([ReadinessItem("a", True), ReadinessItem("b", True)])
    assert not can_activate([ReadinessItem("a", True), ReadinessItem("b", False, "x")])
    assert can_activate([])


def test_default_resolution() -> None:
    first = flow("first", default_for=frozenset({IntakeMode.APPOINTMENT}))
    other = flow("other", default_for=frozenset({IntakeMode.ON_DEMAND}))
    draft = flow("draft", FlowState.DRAFT, default_for=frozenset({IntakeMode.APPOINTMENT}))
    old = flow("old", FlowState.ARCHIVED, default_for=frozenset({IntakeMode.APPOINTMENT}))
    assert resolve_default([first, other, draft, old], IntakeMode.APPOINTMENT) is first
    assert resolve_default([first, other], IntakeMode.ON_DEMAND) is other


def test_default_resolution_errors() -> None:
    first = flow("first", default_for=frozenset({IntakeMode.APPOINTMENT}))
    second = flow("second", default_for=frozenset({IntakeMode.APPOINTMENT}))
    for flows, field in (
        ([first], "flow_default_missing"),
        ([], "flow_default_missing"),
        (
            [flow("d", FlowState.DRAFT, default_for=frozenset({IntakeMode.ON_DEMAND}))],
            "flow_default_missing",
        ),
        ([first, second], "flow_default_ambiguous"),
    ):
        mode = IntakeMode.APPOINTMENT if field.endswith("ambiguous") else IntakeMode.ON_DEMAND
        with pytest.raises(DomainError) as caught:
            resolve_default(flows, mode)
        assert (caught.value.code, caught.value.field) == (ErrorCode.INVALID_CONFIGURATION, field)


def test_explicit_resolution_never_falls_back_to_the_default() -> None:
    default = flow("main", default_for=frozenset({IntakeMode.APPOINTMENT}))
    named = flow("named")
    both = flow("both", current=revision(intake_modes=frozenset(IntakeMode)))
    ondemand = flow("od", current=revision(intake_modes=frozenset({IntakeMode.ON_DEMAND})))
    draft = flow("dr", FlowState.DRAFT)
    old = flow("ol", FlowState.ARCHIVED)
    flows = [default, named, both, ondemand, draft, old]
    assert resolve_explicit(flows, "named", IntakeMode.APPOINTMENT) is named
    assert resolve_explicit(flows, "both", IntakeMode.ON_DEMAND) is both
    for code, mode in (
        ("nope", IntakeMode.APPOINTMENT),
        ("dr", IntakeMode.APPOINTMENT),
        ("ol", IntakeMode.APPOINTMENT),
        ("od", IntakeMode.APPOINTMENT),
        ("named", IntakeMode.ON_DEMAND),
    ):
        with pytest.raises(DomainError) as caught:
            resolve_explicit(flows, code, mode)
        assert (caught.value.code, caught.value.field) == (
            ErrorCode.INVALID_CONFIGURATION,
            "flow_unknown",
        )


def test_no_behaviour_depends_on_a_label() -> None:
    plain = revision()
    odd = revision(labels={"nl": "zzz", "en": "Balie 7"})
    assert reasons(plain, pool()) == reasons(odd, pool())
    assert readiness_policy(odd.interaction_kind) is readiness_policy(plain.interaction_kind)  # type: ignore[arg-type]
    a = flow("one", default_for=frozenset({IntakeMode.APPOINTMENT}), current=plain)
    b = flow("two", default_for=frozenset({IntakeMode.APPOINTMENT}), current=odd)
    assert resolve_default([a], IntakeMode.APPOINTMENT) is a
    assert resolve_default([b], IntakeMode.APPOINTMENT) is b
    assert resolve_explicit([a, b], "two", IntakeMode.APPOINTMENT) is b
