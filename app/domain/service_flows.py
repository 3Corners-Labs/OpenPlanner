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
every non-legacy revision while ``RETENTION_BUILT`` is false, and then while the rollback gate
``ROLLBACK_SAFE`` is shut (reason ``rollback_not_safe``, ADR 0122), so no new flow can be
activated.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, cast

from app.domain.capacity import CapacityPool, PoolMode, guarantees, unguaranteed
from app.domain.contact_center import ContactCenterCapabilities, uncarried
from app.domain.errors import DomainError, ErrorCode, missing_object, outside_organisation
from app.domain.interaction import InteractionKind as InteractionKind  # re-exported
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
"""Dispatch kinds a new flow may use; step 6b of ADR 0121 adds the contact center."""
BUILT_COMPLETION: frozenset[CompletionPolicy] = frozenset({CompletionPolicy.OPERATOR})
"""Completion policies a new flow may use; step 6b of ADR 0121 adds the provider event."""
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

Read when called, never copied (tests flip it with a monkeypatch): ``readiness`` reports
``rollback_not_safe`` while it is false (ADR 0122 step 5), and
``flow_resolution.pool_flows_bookable`` keeps every flow on a counted pool out of the booking
paths.
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


def choosable_actions(kind: InteractionKind | None) -> tuple[HandlingAction, ...]:
    """Return the handling actions the table allows for ``kind``, in the order of the enum.

    Whether one of them is built is ``BUILT_ACTIONS``'s answer, not this table's; a channel not
    chosen yet allows none.
    """
    if kind is None:
        return ()
    allowed = COMBINATIONS[kind].actions
    return tuple(action for action in HandlingAction if action in allowed)


def default_action(kind: InteractionKind | None) -> HandlingAction | None:
    """Return the action a flow of ``kind`` starts with: the first allowed one that is built.

    A channel with one allowed action has no choice at all; for more, the first built one in
    the order of ``HandlingAction`` is the default (the first allowed one when none is built),
    and ``None`` for a channel not chosen yet.
    """
    actions = choosable_actions(kind)
    return next((a for a in actions if a in BUILT_ACTIONS), actions[0] if actions else None)


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

    @property
    def legacy(self) -> bool:
        """Whether this is the wrapper of a pre-flow group: the one definition of a legacy flow."""
        return self.current is not None and self.current.legacy

    @property
    def in_progress(self) -> bool:
        """Whether a published flow has a draft next to its current revision."""
        return self.current is not None and self.draft is not None

    @property
    def defaults_fixed(self) -> bool:
        """Whether code and default entry paths are fixed: the flow has a published revision."""
        return self.current is not None

    @property
    def listed(self) -> FlowRevision:
        """The revision a list shows: the published one, else the draft (never ``None``)."""
        return cast(FlowRevision, self.current or self.draft)  # the constructor refuses neither

    @property
    def working(self) -> FlowRevision:
        """The revision a page is about: the draft when there is one, else the published one."""
        return cast(FlowRevision, self.draft or self.current)  # the constructor refuses neither


def requirement_of(revision: FlowRevision | None, subject: str | None) -> frozenset[int]:
    """Return the skills a booking made under ``revision`` about ``subject`` requires.

    The flow's own skills plus the extra skills of the chosen subject (ADR 0121 point 5); a
    subject the revision does not bind adds nothing, and a booking without a flow requires
    nothing. The revision is the one recorded on the booking, so a later edit of the flow never
    changes what an existing booking requires (A23).
    """
    if revision is None:
        return frozenset()
    extra = revision.subject_skill_ids.get(subject, frozenset()) if subject else frozenset()
    return effective_requirements(revision.required_skill_ids, extra)


def check_code_kept(
    skill_id: int, before: str | None, after: str | None, flows: Iterable[ServiceFlow]
) -> None:
    """Refuse taking a skill's contact-center code away while a flow dispatches on it.

    A flow that is not archived and whose current (published) revision dispatches to the
    contact center and requires the skill, for itself or a subject, needs the code: without it
    every dispatch of its bookings would be refused (ADR 0121 step 4b). Changing the code to
    another one is allowed.

    Raises:
        DomainError: ``configuration_object_in_use`` (field ``contact_center_code``).
    """
    if before is None or after is not None:
        return
    for flow in flows:
        current = flow.current
        if (
            flow.state is not FlowState.ARCHIVED
            and current is not None
            and current.dispatch is DispatchKind.CONTACT_CENTER
            and skill_id in current.all_skill_ids
        ):
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "contact_center_code")


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
    SKILL_UNMAPPED = "skill_unmapped"
    """A contact-center flow requires a skill without a contact-center code (ADR 0121 step 4b):
    an unmapped requirement is an incomplete configuration, never dropped from the payload."""
    SKILLS_NOT_CARRIED = "skills_not_carried"
    """A contact-center flow requires skills, but the service's platform cannot route on skills
    (``ContactCenterCapabilities.skills``, ADR 0121 step 6)."""
    SUBJECT_UNKNOWN = "subject_unknown"
    KIND_MISSING = "kind_missing"
    DISPATCH_MISSING = "dispatch_missing"
    ACTION_NOT_ALLOWED = "action_not_allowed"
    CONTACT_CENTER_MISSING = "contact_center_missing"
    """A flow dispatches to the contact center, but its service has no complete contact-center
    routing (ADR 0121 step 6)."""
    INTERACTION_NOT_CARRIED = "interaction_not_carried"
    """The service's contact-center platform does not carry the flow's interaction kind
    (``ContactCenterCapabilities.interaction_kinds``, ADR 0121 step 6)."""
    LOCATION_MISSING = "location_missing"
    NOT_BUILT_YET = "not_built_yet"
    COMPLETION_MISSING = "completion_missing"
    COMPLETION_LEGACY_ONLY = "completion_legacy_only"
    ROLLBACK_NOT_SAFE = "rollback_not_safe"
    """The rollback gate ``ROLLBACK_SAFE`` is shut: the previous supported version could not
    yet account for the pool slots a new flow writes (ADR 0122 decision 12)."""


FOLLOWING: frozenset[ReadinessReason] = frozenset(
    {ReadinessReason.NOT_BUILT_YET, ReadinessReason.ROLLBACK_NOT_SAFE}
)
"""The reasons an administrator cannot fix: a part of OpenPlanner that follows in a later
version. They keep a checklist from being complete, never make it incomplete."""


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
    revision: FlowRevision,
    pool: CapacityPool | None,
    offered_subjects: Collection[str],
    coded_skills: Collection[int],
    carrier: ContactCenterCapabilities | None,
) -> ReadinessReason | None:
    """Return why the skills check fails, if it does.

    A revision that dispatches to the contact center needs a platform that routes on skills
    (``carrier``, judged only for a non-legacy revision) and a contact-center code for every
    skill it or a subject requires (``coded_skills`` are the ids that have one).
    """
    if revision.capacity_pool_id is None or pool is None:
        return ReadinessReason.POOL_MISSING
    if any(code not in offered_subjects for code in revision.subject_skill_ids):
        return ReadinessReason.SUBJECT_UNKNOWN
    for extra in (frozenset[int](), *revision.subject_skill_ids.values()):
        if not guarantees(pool, effective_requirements(revision.required_skill_ids, extra)):
            return ReadinessReason.POOL_DOES_NOT_GUARANTEE
    if revision.dispatch is not DispatchKind.CONTACT_CENTER or not revision.all_skill_ids:
        return None
    if not revision.legacy and carrier is not None and not carrier.skills:
        return ReadinessReason.SKILLS_NOT_CARRIED
    if revision.all_skill_ids - frozenset(coded_skills):
        return ReadinessReason.SKILL_UNMAPPED
    return None


def missing_guarantees(revision: FlowRevision, pool: CapacityPool | None) -> frozenset[int]:
    """Return the skills ``revision`` needs, for the flow or a subject, that ``pool`` lacks.

    The same requirements the skills check judges (the flow's own skills, and the flow's plus each
    subject's extra ones); a revision without a pool lacks none that it could name.
    """
    if pool is None:
        return frozenset()
    lacking = unguaranteed(pool, revision.required_skill_ids)
    for extra in revision.subject_skill_ids.values():
        lacking |= unguaranteed(pool, effective_requirements(revision.required_skill_ids, extra))
    return lacking


def _carrier_reason(
    revision: FlowRevision, kind: InteractionKind, carrier: ContactCenterCapabilities | None
) -> ReadinessReason | None:
    """Return why the service's contact center cannot take a non-legacy revision's work, if so.

    A legacy revision follows its service's own settings and is judged by the group check of
    ADR 0082, so it is not judged here; nor is a revision for the own queue.
    """
    if revision.legacy or revision.dispatch is not DispatchKind.CONTACT_CENTER:
        return None
    if carrier is None:
        return ReadinessReason.CONTACT_CENTER_MISSING
    if uncarried(carrier, kind, skills=False) is not None:
        return ReadinessReason.INTERACTION_NOT_CARRIED
    return None


def _dispatch_reason(
    revision: FlowRevision, carrier: ContactCenterCapabilities | None
) -> ReadinessReason | None:
    """Return why the dispatch check fails, if it does.

    What the administrator can fix comes before what follows in a later version: a contact
    center that cannot take the work is named before ``not_built_yet``.
    """
    kind = revision.interaction_kind
    if kind is None:
        return ReadinessReason.KIND_MISSING
    if revision.dispatch is None:
        return ReadinessReason.DISPATCH_MISSING
    action = revision.handling_action
    if action is None or action not in COMBINATIONS[kind].actions:
        return ReadinessReason.ACTION_NOT_ALLOWED
    if (reason := _carrier_reason(revision, kind, carrier)) is not None:
        return reason
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
    """Return why the retention check fails, if it does.

    The last point of the checklist covers what happens to a flow's records after the work:
    their closing and deletion (``RETENTION_BUILT``) and whether the previous version could still
    account for them after a rollback (``ROLLBACK_SAFE``, ADR 0121 point 9, ADR 0122 decision 12).
    Both are read when called.
    """
    if revision.legacy:
        return None
    if not RETENTION_BUILT:
        return ReadinessReason.NOT_BUILT_YET
    if not ROLLBACK_SAFE:
        return ReadinessReason.ROLLBACK_NOT_SAFE
    return None


def readiness(
    revision: FlowRevision,
    pool: CapacityPool | None,
    *,
    offered_subjects: Collection[str],
    coded_skills: Collection[int] = (),
    carrier: ContactCenterCapabilities | None = None,
) -> tuple[ReadinessItem, ...]:
    """Return the six checklist items, in order, for activating ``revision``.

    ``pool`` is the revision's capacity pool, ``offered_subjects`` the codes of the group's
    subjects, ``coded_skills`` the ids of the organisation's skills that have a contact-center
    code (only a contact-center revision with skills reads it), ``carrier`` what the platform of
    the service's complete contact-center routing carries (``capabilities_of``; ``None``
    without one; only a non-legacy contact-center revision reads it, ADR 0121 step 6). Values
    outside the ``BUILT_*`` constants fail for a non-legacy revision.
    """
    return (
        _item(ReadinessCheck.DATA, _data_reason(revision)),
        _item(ReadinessCheck.CAPACITY, _capacity_reason(revision, pool)),
        _item(
            ReadinessCheck.SKILLS,
            _skills_reason(revision, pool, offered_subjects, coded_skills, carrier),
        ),
        _item(ReadinessCheck.DISPATCH, _dispatch_reason(revision, carrier)),
        _item(ReadinessCheck.COMPLETION, _completion_reason(revision)),
        _item(ReadinessCheck.RETENTION, _retention_reason(revision)),
    )


def can_activate(items: Sequence[ReadinessItem]) -> bool:
    """Return whether every checklist item is ok."""
    return all(item.ok for item in items)


class ReadinessState(StrEnum):
    """Where a checklist stands as a whole; a stable code, never user-facing text."""

    COMPLETE = "complete"
    """Every point is in order."""

    COMPLETE_EXCEPT_FOLLOWING = "complete_except_following"
    """Every point is in order except points that wait on a part not built yet."""

    INCOMPLETE = "incomplete"
    """At least one point is open for a reason the administrator can fix."""


def readiness_state(items: Sequence[ReadinessItem]) -> ReadinessState:
    """Return where the checklist stands: complete, complete but for parts that follow, or not.

    A point that fails for a reason in ``FOLLOWING`` cannot be fixed by an administrator; it
    does not make the checklist incomplete, but it keeps it from being complete.
    """
    open_items = [item for item in items if not item.ok]
    if not open_items:
        return ReadinessState.COMPLETE
    if all(item.reason in FOLLOWING for item in open_items):
        return ReadinessState.COMPLETE_EXCEPT_FOLLOWING
    return ReadinessState.INCOMPLETE


def can_publish(items: Sequence[ReadinessItem]) -> bool:
    """Return whether a draft with this checklist may be published.

    Every point must be in order except the points that follow (``not_built_yet``): publishing
    does not activate, and activation stays refused until those are built.
    """
    return readiness_state(items) is not ReadinessState.INCOMPLETE


def check_publish_ready(items: Sequence[ReadinessItem]) -> None:
    """Refuse publishing a draft whose checklist has a point the administrator can still fix.

    Raises:
        DomainError: ``invalid_configuration`` (field ``readiness``).
    """
    if not can_publish(items):
        raise _invalid("readiness")


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
    restoring: bool = False,
) -> None:
    """Refuse a revision that refers to what its group's organisation does not offer.

    ``pool`` is the revision's capacity pool as loaded (``None`` when there is none with that
    id), ``skills`` every skill it names (for the flow or a subject) by id as loaded,
    ``already_required`` the skills the revision it is based on requires, ``already_pool_id``
    that revision's pool, ``group_id`` the group the flow belongs to (a legacy pool serves only
    its own group's flows) and ``subject_codes`` the codes of the group's subjects. A revision
    that is not the legacy wrapper draws on a counted pool only: a legacy pool, the group's own
    or another's, answers like one that does not exist. An archived pool or skill may stay where
    it is but is not newly chosen (A28). The location is a plain number until locations are
    built, so a revision names none. A revision that a configuration document *restores* (a
    published one of a flow the installation lacks, recorded as it was) is exempt from the
    counted-pool rule only: what was published then stays as recorded. ``restoring`` says so;
    drafts and newly published revisions never pass it.

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
        if revision.legacy:
            if pool.mode is PoolMode.LEGACY_GROUP and pool.group_id != group_id:
                raise _invalid("capacity_pool_id")
        elif pool.mode is not PoolMode.COUNTED and not restoring:
            raise _invalid("capacity_pool_id")  # a new flow never draws on a service's own staffing
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


@dataclass(frozen=True)
class FlowActions:
    """What the lifecycle allows to do with a flow now; each is allowed or not.

    The one answer the use cases guard with and the pages show buttons from, so no route or
    template works out a lifecycle rule of its own.

    Attributes:
        prepare: Start a new revision as a copy of the published one.
        save: Save the draft revision.
        publish: Publish the draft: every point of its checklist in order but the ones that follow.
        discard: Throw away the draft of a flow that has a published revision; a flow never
            published is deleted instead.
        activate: Activate the flow: only when the revision activation judges is in order.
        archive: Archive an active flow; its draft, if any, goes with it.
        delete: Delete a flow that was never active, with all its revisions.
    """

    prepare: bool = False
    save: bool = False
    publish: bool = False
    discard: bool = False
    activate: bool = False
    archive: bool = False
    delete: bool = False

    @property
    def any(self) -> bool:
        """Whether there is anything to do with the flow now."""
        return (
            self.prepare
            or self.publish
            or self.discard
            or self.activate
            or self.archive
            or self.delete
        )


def flow_actions(
    flow: ServiceFlow,
    activation: Sequence[ReadinessItem] | None,
    draft_readiness: Sequence[ReadinessItem] | None = None,
) -> FlowActions:
    """Return what may be done with ``flow``; ``activation`` is the checklist of its published one.

    ``draft_readiness`` is the checklist of the draft; publishing is allowed only when it is given
    and ``can_publish`` accepts it (a caller that does not judge the draft never publishes).

    ``activation`` is the readiness of the revision activation judges (the published one),
    ``None`` when the flow has none. A legacy flow follows its service's settings and an archived
    flow is history: nothing is allowed for either. A flow reaches ``active`` only by activation
    and never returns to ``draft``, so a flow in state draft was never active and may be deleted;
    one that was active is archived.
    """
    if flow.legacy or flow.state is FlowState.ARCHIVED:
        return FlowActions()
    draft = flow.draft is not None
    never_active = flow.state is FlowState.DRAFT
    return FlowActions(
        prepare=flow.current is not None and not draft,
        save=draft,
        publish=draft and draft_readiness is not None and can_publish(draft_readiness),
        discard=draft and flow.current is not None,
        activate=(never_active and activation is not None and can_activate(activation)),
        archive=flow.state is FlowState.ACTIVE,
        delete=never_active,
    )


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
    always allowed, a legacy flow included. A flow that was never active is never archived: it is
    deleted (``check_flow_delete``). Whether the readiness checklist is green is the
    caller's check (``readiness``), and whether a default path is free is ``check_default_paths``.

    Raises:
        DomainError: ``invalid_configuration`` (field ``state``).
    """
    if (flow.state, new_state) not in _TRANSITIONS:
        raise _invalid("state")
    if flow.current is None and new_state is FlowState.ACTIVE:
        raise _invalid("state")


def check_flow_delete(flow: ServiceFlow, referred_to: bool) -> None:
    """Refuse deleting a flow that was ever active or that a booking refers to.

    A flow reaches ``active`` only by activation and never returns to ``draft``, so a flow in
    state draft was never active, whether or not it has a published revision. ``referred_to`` is
    whether a booking refers to the flow; the adapter loads that fact.

    Raises:
        DomainError: ``invalid_configuration`` (field ``state``) for a flow that is or was active
            (and a legacy flow), ``configuration_object_in_use`` (field ``flow_id``) for one a
            booking refers to.
    """
    if flow.state is not FlowState.DRAFT or flow.legacy:
        raise _invalid("state")
    if referred_to:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "flow_id")


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


def check_built_choices(draft: FlowRevision) -> None:
    """Refuse a draft that chooses what ADR 0121 has not built yet.

    A draft that is not the legacy wrapper may choose only the ``BUILT_*`` constants (read when
    called): the interaction kind, the dispatch, the completion and the handling action. A choice
    not made yet is no error, and the legacy wrapper skips the test.

    Raises:
        DomainError: ``invalid_configuration`` with field ``interaction_kind``, ``dispatch``,
            ``completion`` or ``handling_action``.
    """
    if draft.legacy:
        return
    for name, value, built in (
        ("interaction_kind", draft.interaction_kind, BUILT_KINDS),
        ("dispatch", draft.dispatch, BUILT_DISPATCH),
        ("completion", draft.completion, BUILT_COMPLETION),
        ("handling_action", draft.handling_action, BUILT_ACTIONS),
    ):
        if value is not None and value not in built:
            raise _invalid(name)


def check_draft_content(draft: FlowRevision) -> None:
    """Refuse a draft that is wrong in itself; one that is only incomplete passes.

    The labels need both locales, the duration is an allowed appointment length, a draft that is
    not the legacy wrapper chooses only what is built (``check_built_choices``) and the action
    is one the interaction kind's row of the closed table allows. A choice not made yet is no
    error. A configuration document's schema checks the labels, the duration and the
    combination itself and calls ``check_built_choices`` only.

    Raises:
        DomainError: ``invalid_configuration`` with field ``labels``, ``duration_minutes``,
            ``interaction_kind``, ``dispatch``, ``completion`` or ``handling_action``.
    """
    validate_labels(draft.labels, "labels")
    if draft.duration_minutes is not None and not valid_slot_minutes(draft.duration_minutes):
        raise _invalid("duration_minutes")
    if draft.legacy:
        return
    check_built_choices(draft)
    kind, action = draft.interaction_kind, draft.handling_action
    if action is not None and (kind is None or action not in COMBINATIONS[kind].actions):
        raise _invalid("handling_action")


def _unpublished_draft(flow: ServiceFlow) -> FlowRevision:
    """Return ``flow``'s draft when it is one a save may write.

    Raises:
        DomainError: ``invalid_configuration`` (fields ``draft``, ``published_at``, ``legacy``).
    """
    draft = flow.draft
    if draft is None:
        raise _invalid("draft")
    if draft.published_at is not None:
        raise _invalid("published_at")
    if draft.legacy:
        raise _invalid("legacy")
    return draft


def check_draft_identity(
    flow: ServiceFlow, stored: ServiceFlow | None, code_taken: bool
) -> FlowRevision:
    """Refuse saving ``flow``'s draft when the flow itself does not fit what is stored.

    The identity part of a save: whose draft it is, the state, the code and the default entry
    paths. ``stored`` is the flow with ``flow.id`` inside the caller's scope (``None`` for a new
    flow or an id that is missing or outside it), ``code_taken`` whether another flow of the
    group has the code. A draft is never legacy: the wrapper of a pre-flow group skips the
    readiness tests of what is built, so only the migration and ADR 0121 step 2e write one. A
    new flow is a draft with no current revision. Once a flow has a published revision its code
    and defaults stay as they are; only the draft revision changes. What the draft chooses is
    ``check_draft_content`` (the portal) or ``check_built_choices`` (a configuration document).
    Returns the draft to write.

    Raises:
        DomainError: ``invalid_configuration`` (fields ``draft``, ``published_at``, ``legacy``,
            ``state``, ``revision``, ``code``, ``default_for``), ``configuration_object_missing``
            (field ``flow_id``), ``configuration_out_of_scope`` (field ``group_id``) or
            ``slug_taken`` (field ``flow_code``).
    """
    draft = _unpublished_draft(flow)
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
        if stored.defaults_fixed and (
            (stored.code, stored.default_for) != (flow.code, flow.default_for)
        ):
            raise _invalid("code" if stored.code != flow.code else "default_for")
    if code_taken:
        raise DomainError(ErrorCode.SLUG_TAKEN, "flow_code")
    return draft


def check_draft_save(
    flow: ServiceFlow, stored: ServiceFlow | None, code_taken: bool
) -> FlowRevision:
    """Refuse saving ``flow``'s draft in the portal: its content, then its identity.

    The portal's whole check (``check_draft_content`` then ``check_draft_identity``); the
    adapter loads the facts. Returns the draft to write.

    Raises:
        DomainError: What the two refuse.
    """
    check_draft_content(_unpublished_draft(flow))
    return check_draft_identity(flow, stored, code_taken)
