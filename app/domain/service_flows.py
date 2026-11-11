"""Service flows: how one service is taken in, handled and completed (ADR 0121).

A flow belongs to a group (the service offering) and has numbered, immutable revisions. The
combination of interaction kind, readiness and handling action is a closed table
(``COMBINATIONS``, ADR 0121 point 4). A draft is activated only when the readiness checklist
is all green. Behaviour never depends on a label; labels are display text only.

Until ADR 0121 steps 5 and 6 are built, the checklist deliberately refuses everything that
is not a legacy wrapper (reason ``not_built_yet``), so no new flow can be activated.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from app.domain.capacity import CapacityPool, guarantees
from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import REQUIRED_LOCALES, SUBJECT_CODE
from app.domain.skills import effective_requirements

MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 480


class IntakeMode(StrEnum):
    """How a participant reaches the flow."""

    APPOINTMENT = "appointment"
    ON_DEMAND = "on_demand"


class InteractionKind(StrEnum):
    """What the participant and staff do together."""

    VIDEO = "video"
    TELEPHONE = "telephone"
    IN_PERSON = "in_person"
    NONE = "none"


class ReadinessPolicy(StrEnum):
    """How the system learns the participant is ready."""

    EXPLICIT_CHECK_IN = "explicit_check_in"
    MEDIA_PRESENCE = "media_presence"
    AUTOMATIC = "automatic"


class HandlingAction(StrEnum):
    """What staff do with a request."""

    OPEN_VIDEO = "open_video"
    VIEW_REQUEST = "view_request"
    CALL = "call"
    ASSIGN_WORKSTATION = "assign_workstation"


class CompletionPolicy(StrEnum):
    """What marks a request as done."""

    OPERATOR = "operator"
    PROVIDER_EVENT = "provider_event"
    LEGACY_VIDEO_OBSERVATION = "legacy_video_observation"


class FlowState(StrEnum):
    """Lifecycle of a flow."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class DispatchKind(StrEnum):
    """Where a request is dispatched to staff."""

    OWN_QUEUE = "own_queue"
    CONTACT_CENTER = "contact_center"


@dataclass(frozen=True)
class Combination:
    """The readiness policy and the allowed handling actions of one interaction kind."""

    readiness: ReadinessPolicy
    actions: frozenset[HandlingAction]


COMBINATIONS: Mapping[InteractionKind, Combination] = {
    InteractionKind.VIDEO: Combination(
        ReadinessPolicy.MEDIA_PRESENCE, frozenset({HandlingAction.OPEN_VIDEO})
    ),
    InteractionKind.TELEPHONE: Combination(
        ReadinessPolicy.AUTOMATIC, frozenset({HandlingAction.CALL})
    ),
    InteractionKind.IN_PERSON: Combination(
        ReadinessPolicy.EXPLICIT_CHECK_IN,
        frozenset({HandlingAction.VIEW_REQUEST, HandlingAction.ASSIGN_WORKSTATION}),
    ),
    InteractionKind.NONE: Combination(
        ReadinessPolicy.AUTOMATIC, frozenset({HandlingAction.VIEW_REQUEST})
    ),
}
"""The closed table of ADR 0121 point 4."""


@dataclass(frozen=True)
class FlowRevision:
    """One immutable revision of a flow; a draft may leave not-yet-chosen fields ``None``.

    Attributes:
        flow_id: The flow it belongs to, ``None`` before it is stored.
        revision: Revision number, from 1.
        labels: Display label per locale.
        intake_modes: The entry paths the flow offers.
        interaction_kind: Video, telephone, in person or none.
        duration_minutes: Length of one appointment, 5 to 480.
        capacity_pool_id: The pool the flow draws on.
        required_skill_ids: Skills every handler needs.
        subject_skill_ids: Subject code to extra skills; the keys are the offered subjects.
        dispatch: Own queue or contact center.
        handling_action: What staff do with a request.
        completion: What marks a request as done.
        location_id: The location, needed for assigning a workstation.
        collects_email: Whether the form asks for an email address.
        legacy: Whether this is the wrapper of a pre-flow group.
    """

    flow_id: int | None
    revision: int
    labels: Mapping[str, str]
    intake_modes: frozenset[IntakeMode]
    interaction_kind: InteractionKind | None
    duration_minutes: int | None
    capacity_pool_id: int | None
    required_skill_ids: frozenset[int]
    subject_skill_ids: Mapping[str, frozenset[int]]
    dispatch: DispatchKind | None
    handling_action: HandlingAction | None
    completion: CompletionPolicy | None
    location_id: int | None = None
    collects_email: bool = True
    legacy: bool = False


@dataclass(frozen=True)
class ServiceFlow:
    """A flow of a group with its current revision.

    Attributes:
        id: Database id, ``None`` before it is stored.
        group_id: The group (service offering) it belongs to.
        code: Stable code, the same grammar as a subject code.
        state: Draft, active or archived.
        default_for: The entry paths this flow is the default of.
        current: The current revision.
    """

    id: int | None
    group_id: int
    code: str
    state: FlowState
    default_for: frozenset[IntakeMode]
    current: FlowRevision

    def __post_init__(self) -> None:
        """Validate the code (``flow_code``)."""
        if not SUBJECT_CODE.fullmatch(self.code):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "flow_code")


def readiness_policy(kind: InteractionKind) -> ReadinessPolicy:
    """Return the readiness policy implied by an interaction kind."""
    return COMBINATIONS[kind].readiness


@dataclass(frozen=True)
class ReadinessItem:
    """One line of the readiness checklist.

    Attributes:
        code: Which check (``data``, ``capacity``, ``skills``, ``dispatch``, ``completion``,
            ``retention``).
        ok: Whether it passes.
        reason: A stable reason code when not ok, else ``None``; never user-facing text.
    """

    code: str
    ok: bool
    reason: str | None = None


def _item(code: str, reason: str | None) -> ReadinessItem:
    """Return the item ``code``, ok exactly when there is no ``reason``."""
    return ReadinessItem(code, reason is None, reason)


def _data_reason(revision: FlowRevision) -> str | None:
    """Return why the data check fails, if it does."""
    if any(not revision.labels.get(locale, "").strip() for locale in REQUIRED_LOCALES):
        return "labels_missing"
    if not revision.intake_modes:
        return "intake_missing"
    return None


def _capacity_reason(revision: FlowRevision, pool: CapacityPool | None) -> str | None:
    """Return why the capacity check fails, if it does."""
    if revision.capacity_pool_id is None or pool is None:
        return "pool_missing"
    if pool.archived_at is not None:
        return "pool_archived"
    duration = revision.duration_minutes
    if duration is None or not MIN_DURATION_MINUTES <= duration <= MAX_DURATION_MINUTES:
        return "duration"
    return None


def _skills_reason(
    revision: FlowRevision, pool: CapacityPool | None, offered_subjects: Collection[str]
) -> str | None:
    """Return why the skills check fails, if it does."""
    if revision.capacity_pool_id is None or pool is None:
        return "pool_missing"
    if any(code not in offered_subjects for code in revision.subject_skill_ids):
        return "subject_unknown"
    for extra in (frozenset[int](), *revision.subject_skill_ids.values()):
        if not guarantees(pool, effective_requirements(revision.required_skill_ids, extra)):
            return "pool_does_not_guarantee"
    return None


def _dispatch_reason(revision: FlowRevision) -> str | None:
    """Return why the dispatch check fails, if it does."""
    kind = revision.interaction_kind
    if kind is None:
        return "kind_missing"
    if revision.dispatch is None:
        return "dispatch_missing"
    action = revision.handling_action
    if action is None or action not in COMBINATIONS[kind].actions:
        return "action_not_allowed"
    if (
        kind is InteractionKind.IN_PERSON
        and action is HandlingAction.ASSIGN_WORKSTATION
        and revision.location_id is None
    ):
        return "location_missing"
    if not revision.legacy and (
        kind is InteractionKind.VIDEO or revision.dispatch is DispatchKind.CONTACT_CENTER
    ):
        return "not_built_yet"
    return None


def _completion_reason(revision: FlowRevision) -> str | None:
    """Return why the completion check fails, if it does."""
    completion = revision.completion
    if completion is None:
        return "completion_missing"
    if not revision.legacy:
        if completion is CompletionPolicy.LEGACY_VIDEO_OBSERVATION:
            return "completion_legacy_only"
        if completion is CompletionPolicy.PROVIDER_EVENT:
            return "not_built_yet"
    return None


def readiness(
    revision: FlowRevision, pool: CapacityPool | None, *, offered_subjects: Collection[str]
) -> tuple[ReadinessItem, ...]:
    """Return the six checklist items, in order, for activating ``revision``.

    ``pool`` is the revision's capacity pool, ``offered_subjects`` the codes of the group's
    subjects. The retention item fails for every non-legacy revision until the watchdog and
    deletion of ADR 0121 point 7 exist (step 5), so no new flow can be activated before.
    """
    return (
        _item("data", _data_reason(revision)),
        _item("capacity", _capacity_reason(revision, pool)),
        _item("skills", _skills_reason(revision, pool, offered_subjects)),
        _item("dispatch", _dispatch_reason(revision)),
        _item("completion", _completion_reason(revision)),
        _item("retention", None if revision.legacy else "not_built_yet"),
    )


def can_activate(items: Sequence[ReadinessItem]) -> bool:
    """Return whether every checklist item is ok."""
    return all(item.ok for item in items)


def legacy_revision(
    *,
    labels: Mapping[str, str],
    intake_mode: IntakeMode,
    duration_minutes: int,
    capacity_pool_id: int,
    dispatch: DispatchKind,
) -> FlowRevision:
    """Return revision 1 of the wrapper flow of a pre-flow group (ADR 0121 point 8)."""
    return FlowRevision(
        flow_id=None,
        revision=1,
        labels=labels,
        intake_modes=frozenset({intake_mode}),
        interaction_kind=InteractionKind.VIDEO,
        duration_minutes=duration_minutes,
        capacity_pool_id=capacity_pool_id,
        required_skill_ids=frozenset(),
        subject_skill_ids={},
        dispatch=dispatch,
        handling_action=HandlingAction.OPEN_VIDEO,
        completion=CompletionPolicy.LEGACY_VIDEO_OBSERVATION,
        legacy=True,
    )


def next_revision(current: FlowRevision, **changes: Any) -> FlowRevision:  # noqa: ANN401
    """Return a copy of ``current`` with the changes and ``revision + 1``; never mutates."""
    return replace(current, **changes, revision=current.revision + 1)


def resolve_default(flows: Sequence[ServiceFlow], mode: IntakeMode) -> ServiceFlow:
    """Return the active flow that is the default of entry path ``mode``.

    Raises:
        DomainError: ``invalid_configuration`` with field ``flow_default_missing`` when there
            is none, ``flow_default_ambiguous`` when there are several.
    """
    found = [f for f in flows if f.state is FlowState.ACTIVE and mode in f.default_for]
    if not found:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "flow_default_missing")
    if len(found) > 1:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "flow_default_ambiguous")
    return found[0]


def resolve_explicit(flows: Sequence[ServiceFlow], code: str, mode: IntakeMode) -> ServiceFlow:
    """Return the active flow ``code`` that offers ``mode``; never falls back to the default.

    Raises:
        DomainError: ``invalid_configuration`` with field ``flow_unknown`` for an unknown,
            draft or archived flow, or one that does not offer ``mode``.
    """
    for flow in flows:
        if (
            flow.code == code
            and flow.state is FlowState.ACTIVE
            and mode in flow.current.intake_modes
        ):
            return flow
    raise DomainError(ErrorCode.INVALID_CONFIGURATION, "flow_unknown")
