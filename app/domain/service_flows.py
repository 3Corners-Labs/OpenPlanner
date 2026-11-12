"""Service flows: how one service is taken in, handled and completed (ADR 0121).

A flow belongs to a group (the service offering) and has numbered, immutable revisions. The
combination of interaction kind, readiness and handling action is a closed table
(``COMBINATIONS``, ADR 0121 point 4). A draft is activated only when the readiness checklist
is all green. Behaviour never depends on a label; labels are display text only.

A published flow is changed by a *draft* revision next to its current one (``ServiceFlow.draft``);
publishing the draft makes it the current revision and the old one stays for the bookings made
under it.

Until ADR 0121 steps 5 and 6 are built, the checklist refuses every value of a non-legacy
revision that is outside the ``BUILT_*`` constants below (reason ``not_built_yet``), and
every non-legacy revision while ``RETENTION_BUILT`` is false, so no new flow can be activated.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from app.domain.capacity import CapacityPool, PoolMode, guarantees
from app.domain.errors import DomainError, ErrorCode, missing_object, outside_organisation
from app.domain.routing import SUBJECT_CODE, validate_labels
from app.domain.scheduling import valid_slot_minutes
from app.domain.skills import Skill, check_skills_of, effective_requirements


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
BUILT_ACTIONS: frozenset[HandlingAction] = frozenset(
    {HandlingAction.OPEN_VIDEO, HandlingAction.VIEW_REQUEST, HandlingAction.CALL}
)
"""Handling actions a new flow may use; assigning a workstation needs locations, not built."""
RETENTION_BUILT = False
"""Whether the watchdog and deletion of ADR 0121 point 7 exist; step 5 sets it true.

Each later step of ADR 0121 widens exactly these five constants and nothing else here. A
legacy revision skips all five tests.
"""
ROLLBACK_SAFE: Final = False
"""The rollback gate of ADR 0122 (design section 5.2): may a non-legacy flow be activated?

A non-legacy flow writes pool slots (and later occupations and requests) that the previous
supported minor does not know; it would count their bookings in a service's legacy slot and
never respect the pool across services. So a flow may be activated only in an image whose
previous supported minor already holds the readers and the accounting of every record it writes.
This stays false until the release after the last reader-adding step, and until the upgrade
matrix has proven it: activate a flow, book on the pool, roll back to the previous minor, book
and claim there, and check that no unit was sold twice (A30). Setting it true is a decision.

It is not wired into ``readiness`` yet: step 5 of ADR 0122 adds the reason that reads it, next
to the booking path that needs it.
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

    @property
    def all_skill_ids(self) -> frozenset[int]:
        """Return every skill the revision names: required by the flow or by one of its subjects."""
        return self.required_skill_ids.union(*self.subject_skill_ids.values())


@dataclass(frozen=True)
class ServiceFlow:
    """A flow of a group with its published revision and, perhaps, a draft of the next one.

    Attributes:
        id: Database id, ``None`` before it is stored.
        group_id: The group (service offering) it belongs to.
        code: Stable code, the same grammar as a subject code.
        state: Draft, active or archived; an active flow always has a current revision.
        default_for: The entry paths this flow is the default of.
        current: The published revision in effect, ``None`` for a flow never published.
        draft: The unpublished revision being edited, ``None`` when nobody is editing.
    """

    id: int | None
    group_id: int
    code: str
    state: FlowState
    default_for: frozenset[IntakeMode]
    current: FlowRevision | None
    draft: FlowRevision | None = None

    def __post_init__(self) -> None:
        """Validate the revisions, the code, ``default_for`` and the state.

        Raises:
            DomainError: ``invalid_configuration`` with field ``revision`` (neither revision),
                ``flow_code``, ``default_for``, ``state`` (active without a current revision),
                ``flow_id`` or ``published_at`` (a stored flow's current revision is published,
                its draft is not).
        """
        shown = self.current or self.draft
        if shown is None:
            raise _invalid("revision")
        if not SUBJECT_CODE.fullmatch(self.code):
            raise _invalid("flow_code")
        if not self.default_for <= shown.intake_modes:
            raise _invalid("default_for")
        if self.state is FlowState.ACTIVE and self.current is None:
            raise _invalid("state")
        for revision in (self.current, self.draft):
            if revision and None not in (self.id, revision.flow_id) and revision.flow_id != self.id:
                raise _invalid("flow_id")
        if self.id is not None and (
            (self.current is not None and self.current.published_at is None)
            or (self.draft is not None and self.draft.published_at is not None)
        ):
            raise _invalid("published_at")


def revisions_in_effect(flow: ServiceFlow) -> tuple[FlowRevision, ...]:
    """Return the revisions of ``flow`` that count: the current one and the draft.

    The one definition of "in effect" for what a flow draws on and requires: nothing in an older
    revision counts (ADR 0121 point 3). The SQL twin is ``flow_usage.REVISION_IN_EFFECT``.
    """
    return tuple(revision for revision in (flow.current, flow.draft) if revision is not None)


def draws_on_pool(flow: ServiceFlow, pool_id: int) -> bool:
    """Return whether ``flow`` draws on the pool: not archived, and a revision in effect names it.

    The one definition of "drawn on by a flow" that a pool is archived and edited by; the SQL
    twin is ``CapacityPoolQueries.flows_on_pool``.
    """
    return flow.state is not FlowState.ARCHIVED and any(
        revision.capacity_pool_id == pool_id for revision in revisions_in_effect(flow)
    )


def skills_required_on_pool(flows: Iterable[ServiceFlow], pool_id: int) -> frozenset[int]:
    """Return the skills the flows drawing on the pool require, for the flow or a subject.

    Only a revision in effect that names the pool counts, in a flow that is not archived. The SQL
    twin is ``flow_usage.skills_required_on_pool``.
    """
    return frozenset().union(
        *(
            revision.all_skill_ids
            for flow in flows
            if flow.state is not FlowState.ARCHIVED
            for revision in revisions_in_effect(flow)
            if revision.capacity_pool_id == pool_id
        )
    )


def check_revision_editable(published_at: datetime | None, field: str = "revision") -> None:
    """Refuse changing or removing a revision that is published: it is history.

    The one home of "a published revision is immutable". ``published_at`` is the stored
    revision's, ``None`` for one that is new or still a draft. A document, the writer of a
    document and the portal's save all call it.

    Raises:
        DomainError: ``configuration_revision_published`` naming ``field``.
    """
    if published_at is not None:
        raise DomainError(ErrorCode.CONFIGURATION_REVISION_PUBLISHED, field)


def check_combination(kind: InteractionKind | None, action: HandlingAction | None) -> None:
    """Refuse a handling action that the interaction kind's row of the closed table does not allow.

    Nothing is refused while either is still open (a draft).

    Raises:
        DomainError: ``invalid_configuration`` (field ``handling_action``).
    """
    if kind is not None and action is not None and action not in COMBINATIONS[kind].actions:
        raise _invalid("handling_action")


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
    if not revision.legacy and (
        kind not in BUILT_KINDS
        or revision.dispatch not in BUILT_DISPATCH
        or action not in BUILT_ACTIONS
    ):
        return ReadinessReason.NOT_BUILT_YET
    if action is HandlingAction.ASSIGN_WORKSTATION and revision.location_id is None:
        return ReadinessReason.LOCATION_MISSING
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


def check_flow_references(
    group_organisation_id: int,
    revision: FlowRevision,
    pool: CapacityPool | None,
    skills: Mapping[int, Skill],
    already_required: frozenset[int],
    subject_codes: Collection[str],
    *,
    group_id: int,
    already_pool_id: int | None = None,
) -> None:
    """Refuse a revision that refers to what its group's organisation does not offer.

    ``pool`` is the revision's capacity pool as loaded (``None`` when there is none with that
    id), ``skills`` every skill it names (for the flow or a subject) by id as loaded,
    ``already_required`` the skills the revision it is based on requires, ``already_pool_id``
    that revision's pool, ``group_id`` the group the flow belongs to (a legacy pool serves only
    its own group's flows) and ``subject_codes`` the codes of the group's subjects. An archived
    pool or skill may stay where it is but is not newly chosen (A28). The location is a plain
    number until locations are built, so a revision names none.

    Raises:
        DomainError: ``configuration_object_missing`` or ``configuration_out_of_scope`` (fields
            ``capacity_pool_id``, ``required_skill_ids``), ``invalid_configuration`` (fields
            ``capacity_pool_id``, ``required_skill_ids``, ``subject_skill_ids``,
            ``location_id``).
    """
    if revision.capacity_pool_id is not None:
        if pool is None:
            raise missing_object("capacity_pool_id")
        if pool.organisation_id != group_organisation_id:
            raise outside_organisation("capacity_pool_id")
        if pool.mode is PoolMode.LEGACY_GROUP and pool.group_id != group_id:
            raise _invalid("capacity_pool_id")
        if pool.archived_at is not None and revision.capacity_pool_id != already_pool_id:
            raise _invalid("capacity_pool_id")
    if any(code not in subject_codes for code in revision.subject_skill_ids):
        raise _invalid("subject_skill_ids")
    check_skills_of(
        group_organisation_id,
        revision.all_skill_ids,
        skills,
        already_required,
        "required_skill_ids",
    )
    if revision.location_id is not None:
        raise _invalid("location_id")


def check_default_paths(flow: ServiceFlow, other_active_defaults: Collection[IntakeMode]) -> None:
    """Refuse activating ``flow`` when another active flow of its group is the default of a path.

    ``other_active_defaults`` are the entry paths the group's other active flows are the
    default of; the partial unique indexes of the schema stay as the backstop.

    Raises:
        DomainError: ``configuration_object_exists`` (field ``default_for``).
    """
    if flow.default_for & frozenset(other_active_defaults):
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_EXISTS, "default_for")


_TRANSITIONS: frozenset[tuple[FlowState, FlowState]] = frozenset(
    {
        (FlowState.DRAFT, FlowState.ACTIVE),
        (FlowState.ACTIVE, FlowState.ARCHIVED),
        (FlowState.ARCHIVED, FlowState.ACTIVE),
    }
)


def check_state_change(flow: ServiceFlow, new_state: FlowState) -> None:
    """Refuse a change of state the lifecycle does not have.

    Draft to active and archived to active need a published revision; active to archived is
    always allowed, a legacy flow included. Whether the readiness checklist is green is the
    caller's check (``readiness``), and whether a default path is free is ``check_default_paths``.

    Raises:
        DomainError: ``invalid_configuration`` (field ``state``).
    """
    if (flow.state, new_state) not in _TRANSITIONS:
        raise _invalid("state")
    if new_state is FlowState.ACTIVE and flow.current is None:
        raise _invalid("state")


def check_publishable(flow: ServiceFlow) -> FlowRevision:
    """Refuse publishing a flow without a draft, or a draft that drops a default entry path.

    Returns the draft to publish.

    Raises:
        DomainError: ``configuration_object_missing`` (field ``draft``) without a draft,
            ``invalid_configuration`` (field ``default_for``) when the draft no longer offers a
            path the flow is the default of.
    """
    if flow.draft is None:
        raise missing_object("draft")
    if not flow.default_for <= flow.draft.intake_modes:
        raise _invalid("default_for")
    return flow.draft


def check_draft_save(
    flow: ServiceFlow, stored: ServiceFlow | None, code_taken: bool
) -> FlowRevision:
    """Refuse saving ``flow``'s draft given what is stored; the adapter loads the facts.

    ``stored`` is the flow with ``flow.id`` inside the caller's scope (``None`` for a new flow
    or an id that is missing or outside it), ``code_taken`` whether another flow of the group
    has the code. A draft is never legacy: the wrapper of a pre-flow group skips the readiness
    tests of what is built, so only the migration and ADR 0121 step 2e write one. Returns the
    draft to write. A new flow is a draft with no current revision. Once a flow has a published
    revision its code and defaults stay as they are; only the draft revision changes.

    Raises:
        DomainError: ``invalid_configuration`` (fields ``draft``, ``published_at``, ``legacy``,
            ``state``, ``revision``, ``flow_code``, ``code``, ``default_for``),
            ``configuration_object_missing`` (field ``flow_id``), ``configuration_out_of_scope``
            (field ``group_id``) or ``slug_taken`` (field ``flow_code``).
    """
    draft = flow.draft
    if draft is None:
        raise _invalid("draft")
    if draft.published_at is not None:
        raise _invalid("published_at")
    if draft.legacy:
        raise _invalid("legacy")
    if flow.id is None:
        if flow.state is not FlowState.DRAFT:
            raise _invalid("state")
        if flow.current is not None:
            raise _invalid("revision")
    elif stored is None:
        raise missing_object("flow_id")
    else:
        if stored.group_id != flow.group_id:
            raise outside_organisation("group_id")
        if stored.current is not None and (
            (stored.code, stored.default_for) != (flow.code, flow.default_for)
        ):
            raise _invalid("code" if stored.code != flow.code else "default_for")
    if code_taken:
        raise DomainError(ErrorCode.SLUG_TAKEN, "flow_code")
    return draft
