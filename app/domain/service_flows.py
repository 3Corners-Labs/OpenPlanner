"""Service flows: how one service is taken in, handled and completed (ADR 0121).

A flow belongs to a group (the service offering) and has numbered, immutable revisions. The
combination of interaction kind, readiness and handling action is a closed table
(``COMBINATIONS``, ADR 0121 point 4). A draft is activated only when the readiness checklist
is all green. Behaviour never depends on a label; labels are display text only.

Until ADR 0121 steps 5 and 6 are built, the checklist refuses every value of a non-legacy
revision that is outside the ``BUILT_*`` constants below (reason ``not_built_yet``), and
every non-legacy revision while ``RETENTION_BUILT`` is false, so no new flow can be activated.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.capacity import CapacityPool, guarantees
from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import SUBJECT_CODE, validate_labels
from app.domain.scheduling import valid_slot_minutes
from app.domain.skills import effective_requirements


def _invalid(name: str) -> DomainError:
    """Return the configuration error for ``name``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, name)


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


BUILT_KINDS: frozenset[InteractionKind] = frozenset(
    {InteractionKind.TELEPHONE, InteractionKind.IN_PERSON, InteractionKind.NONE}
)
"""Interaction kinds a new (non-legacy) flow may use; step 5 of ADR 0121 adds video."""
BUILT_DISPATCH: frozenset[DispatchKind] = frozenset({DispatchKind.OWN_QUEUE})
"""Dispatch kinds a new flow may use; step 6 of ADR 0121 adds the contact center."""
BUILT_COMPLETION: frozenset[CompletionPolicy] = frozenset({CompletionPolicy.OPERATOR})
"""Completion policies a new flow may use; step 6 of ADR 0121 adds the provider event."""
RETENTION_BUILT = False
"""Whether the watchdog and deletion of ADR 0121 point 7 exist; step 5 sets it true.

Each later step of ADR 0121 widens exactly these four constants and nothing else here. A
legacy revision skips all four tests.
"""


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
        duration_minutes: Length of one appointment, a multiple of 5 from 5 to 480.
        capacity_pool_id: The pool the flow draws on.
        required_skill_ids: Skills every handler needs.
        subject_skill_ids: Subject code to extra skills; the keys are the offered subjects.
        dispatch: Own queue or contact center.
        handling_action: What staff do with a request.
        completion: What marks a request as done.
        location_id: The location, needed for assigning a workstation.
        collects_email: Whether the form asks for an email address.
        legacy: Whether this is the wrapper of a pre-flow group.
        published_at: When the revision was published (UTC), ``None`` while it is a draft; a
            published revision never changes.
        published_by: Staff id of whoever published it, ``None`` for a draft or the backfill.
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
    published_at: datetime | None = None
    published_by: int | None = None

    def __post_init__(self) -> None:
        """Validate the revision number (``revision``)."""
        if self.revision < 1:
            raise _invalid("revision")


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
        """Validate the code (``flow_code``), ``default_for`` and the revision's ``flow_id``."""
        if not SUBJECT_CODE.fullmatch(self.code):
            raise _invalid("flow_code")
        if not self.default_for <= self.current.intake_modes:
            raise _invalid("default_for")
        if None not in (self.id, self.current.flow_id) and self.current.flow_id != self.id:
            raise _invalid("flow_id")


class ReadinessCheck(StrEnum):
    """The six checks of the readiness checklist, in display order."""

    DATA = "data"
    CAPACITY = "capacity"
    SKILLS = "skills"
    DISPATCH = "dispatch"
    COMPLETION = "completion"
    RETENTION = "retention"


class ReadinessReason(StrEnum):
    """Why a check fails; a stable code, never user-facing text."""

    LABELS_INVALID = "labels_invalid"
    INTAKE_MISSING = "intake_missing"
    POOL_MISSING = "pool_missing"
    POOL_ARCHIVED = "pool_archived"
    DURATION = "duration"
    POOL_DOES_NOT_GUARANTEE = "pool_does_not_guarantee"
    SUBJECT_UNKNOWN = "subject_unknown"
    KIND_MISSING = "kind_missing"
    DISPATCH_MISSING = "dispatch_missing"
    ACTION_NOT_ALLOWED = "action_not_allowed"
    LOCATION_MISSING = "location_missing"
    NOT_BUILT_YET = "not_built_yet"
    COMPLETION_MISSING = "completion_missing"
    COMPLETION_LEGACY_ONLY = "completion_legacy_only"


@dataclass(frozen=True)
class ReadinessItem:
    """One line of the readiness checklist.

    Attributes:
        code: Which check.
        ok: Whether it passes.
        reason: The reason when not ok, else ``None``.
    """

    code: ReadinessCheck
    ok: bool
    reason: ReadinessReason | None = None


def _item(code: ReadinessCheck, reason: ReadinessReason | None) -> ReadinessItem:
    """Return the item ``code``, ok exactly when there is no ``reason``."""
    return ReadinessItem(code, reason is None, reason)


def _labels_valid(labels: Mapping[str, str]) -> bool:
    """Return whether ``labels`` pass the shared label rule."""
    try:
        validate_labels(labels, "labels")
    except DomainError:
        return False
    return True


def _data_reason(revision: FlowRevision) -> ReadinessReason | None:
    """Return why the data check fails, if it does."""
    if not _labels_valid(revision.labels):
        return ReadinessReason.LABELS_INVALID
    if not revision.intake_modes:
        return ReadinessReason.INTAKE_MISSING
    return None


def _capacity_reason(revision: FlowRevision, pool: CapacityPool | None) -> ReadinessReason | None:
    """Return why the capacity check fails, if it does."""
    if revision.capacity_pool_id is None or pool is None:
        return ReadinessReason.POOL_MISSING
    if pool.archived_at is not None:
        return ReadinessReason.POOL_ARCHIVED
    duration = revision.duration_minutes
    if duration is None or not valid_slot_minutes(duration):
        return ReadinessReason.DURATION
    return None


def _skills_reason(
    revision: FlowRevision, pool: CapacityPool | None, offered_subjects: Collection[str]
) -> ReadinessReason | None:
    """Return why the skills check fails, if it does."""
    if revision.capacity_pool_id is None or pool is None:
        return ReadinessReason.POOL_MISSING
    if any(code not in offered_subjects for code in revision.subject_skill_ids):
        return ReadinessReason.SUBJECT_UNKNOWN
    for extra in (frozenset[int](), *revision.subject_skill_ids.values()):
        if not guarantees(pool, effective_requirements(revision.required_skill_ids, extra)):
            return ReadinessReason.POOL_DOES_NOT_GUARANTEE
    return None


def _dispatch_reason(revision: FlowRevision) -> ReadinessReason | None:
    """Return why the dispatch check fails, if it does."""
    kind = revision.interaction_kind
    if kind is None:
        return ReadinessReason.KIND_MISSING
    if revision.dispatch is None:
        return ReadinessReason.DISPATCH_MISSING
    action = revision.handling_action
    if action is None or action not in COMBINATIONS[kind].actions:
        return ReadinessReason.ACTION_NOT_ALLOWED
    if action is HandlingAction.ASSIGN_WORKSTATION and revision.location_id is None:
        return ReadinessReason.LOCATION_MISSING
    if not revision.legacy and (kind not in BUILT_KINDS or revision.dispatch not in BUILT_DISPATCH):
        return ReadinessReason.NOT_BUILT_YET
    return None


def _completion_reason(revision: FlowRevision) -> ReadinessReason | None:
    """Return why the completion check fails, if it does."""
    completion = revision.completion
    if completion is None:
        return ReadinessReason.COMPLETION_MISSING
    if revision.legacy:
        return None
    if completion is CompletionPolicy.LEGACY_VIDEO_OBSERVATION:
        return ReadinessReason.COMPLETION_LEGACY_ONLY
    if completion not in BUILT_COMPLETION:
        return ReadinessReason.NOT_BUILT_YET
    return None


def _retention_reason(revision: FlowRevision) -> ReadinessReason | None:
    """Return why the retention check fails, if it does."""
    if revision.legacy or RETENTION_BUILT:
        return None
    return ReadinessReason.NOT_BUILT_YET


def readiness(
    revision: FlowRevision, pool: CapacityPool | None, *, offered_subjects: Collection[str]
) -> tuple[ReadinessItem, ...]:
    """Return the six checklist items, in order, for activating ``revision``.

    ``pool`` is the revision's capacity pool, ``offered_subjects`` the codes of the group's
    subjects. Values outside the ``BUILT_*`` constants fail for a non-legacy revision.
    """
    return (
        _item(ReadinessCheck.DATA, _data_reason(revision)),
        _item(ReadinessCheck.CAPACITY, _capacity_reason(revision, pool)),
        _item(ReadinessCheck.SKILLS, _skills_reason(revision, pool, offered_subjects)),
        _item(ReadinessCheck.DISPATCH, _dispatch_reason(revision)),
        _item(ReadinessCheck.COMPLETION, _completion_reason(revision)),
        _item(ReadinessCheck.RETENTION, _retention_reason(revision)),
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
