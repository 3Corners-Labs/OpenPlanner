"""The readiness checklist of a flow revision, and what may be done with a flow (ADR 0121).

Seven points judge whether a revision may be published and activated: its data, its capacity,
its skills, its dispatch, its completion, the retention of its records and the rollback gate.
Each point names a stable reason when it fails (``ReadinessReason``), never user-facing text.
``flow_actions`` is the one answer, from those checklists, of what the lifecycle allows now.

The ``BUILT_*`` constants, ``RETENTION_BUILT`` and ``ROLLBACK_SAFE`` stay in
``app.domain.service_flows`` and are read from there when called, so a test that widens one with
a monkeypatch reaches the checklist too. Split from ``service_flows`` (ADR 0121 step 6b) so the
flow model and the checklist each stay one module of their own.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain import service_flows as flows
from app.domain.capacity import CapacityPool, guarantees, unguaranteed
from app.domain.contact_center import ContactCenterCapabilities
from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import validate_labels
from app.domain.scheduling import valid_slot_minutes
from app.domain.service_flows import (
    COMBINATIONS,
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ServiceFlow,
)
from app.domain.skills import effective_requirements


def _invalid(name: str) -> DomainError:
    """Return the configuration error for ``name``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, name)


class ReadinessCheck(StrEnum):
    """The seven checks of the readiness checklist, in display order."""

    DATA = "data"
    CAPACITY = "capacity"
    SKILLS = "skills"
    DISPATCH = "dispatch"
    COMPLETION = "completion"
    RETENTION = "retention"
    ROLLBACK = "rollback"
    """Whether the previous supported version can account for what the flow writes (ADR 0122
    decision 12); its own point since ADR 0121 step 5b."""


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
    COMPLETION_NOT_FOR_DISPATCH = "completion_not_for_dispatch"
    """The completion does not belong to the dispatch: the operator completes own-queue work,
    the platform's event contact-center work (``COMPLETION_OF``, ADR 0121 step 6b)."""
    ROLLBACK_NOT_SAFE = "rollback_not_safe"
    """The rollback gate ``ROLLBACK_SAFE`` is shut: the previous supported version could not
    yet account for the pool slots a new flow writes (ADR 0122 decision 12)."""
    CONTACT_CENTER_NOT_READY = "contact_center_not_ready"
    """The flow dispatches to the contact center, which waits for the go-live items G13 to G15
    (``CONTACT_CENTER_FLOWS_READY``, product owner 2026-11-14)."""


FOLLOWING: frozenset[ReadinessReason] = frozenset(
    {
        ReadinessReason.NOT_BUILT_YET,
        ReadinessReason.ROLLBACK_NOT_SAFE,
        ReadinessReason.CONTACT_CENTER_NOT_READY,
    }
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
    if not carrier.carries(kind):
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
        kind not in flows.BUILT_KINDS
        or revision.dispatch not in flows.BUILT_DISPATCH
        or action not in flows.BUILT_ACTIONS
        or _immediate_at_contact_center(revision)
    ):
        return ReadinessReason.NOT_BUILT_YET
    if action is HandlingAction.ASSIGN_WORKSTATION and revision.location_id is None:
        return ReadinessReason.LOCATION_MISSING
    return None


def _immediate_at_contact_center(revision: FlowRevision) -> bool:
    """Return whether the flow would send immediate work to the contact center.

    Not built (ADR 0121 step 7, choice I8): the contact center needs a reference for work
    without a booking and a rule to match its late events; immediate work goes to the own queue.
    """
    return (
        IntakeMode.ON_DEMAND in revision.intake_modes
        and revision.dispatch is DispatchKind.CONTACT_CENTER
    )


def _completion_reason(revision: FlowRevision) -> ReadinessReason | None:
    """Return why the completion check fails, if it does."""
    completion = revision.completion
    if completion is None:
        return ReadinessReason.COMPLETION_MISSING
    if revision.legacy:
        return None
    if completion is CompletionPolicy.LEGACY_VIDEO_OBSERVATION:
        return ReadinessReason.COMPLETION_LEGACY_ONLY
    if completion not in flows.BUILT_COMPLETION:
        return ReadinessReason.NOT_BUILT_YET
    dispatch = revision.dispatch
    if dispatch is not None and flows.COMPLETION_OF.get(dispatch) is not completion:
        return ReadinessReason.COMPLETION_NOT_FOR_DISPATCH
    return None


def _retention_reason(revision: FlowRevision) -> ReadinessReason | None:
    """Return why the retention check fails, if it does.

    It covers what happens to a flow's records after the work: their closing and deletion
    (``RETENTION_BUILT``, ADR 0121 point 7), read when called.
    """
    if revision.legacy or flows.RETENTION_BUILT:
        return None
    return ReadinessReason.NOT_BUILT_YET


def _rollback_reason(revision: FlowRevision) -> ReadinessReason | None:
    """Return why the rollback check fails, if it does.

    Whether the previous supported version could still account for the flow's records after a
    rollback (``ROLLBACK_SAFE``, ADR 0121 point 9, ADR 0122 decision 12), and, once it can,
    whether a flow that dispatches to the contact center may go live
    (``CONTACT_CENTER_FLOWS_READY``, checklist G13 to G15); both read when called. This is the
    one place the contact-center rule is applied, so the portal and the configuration import
    both meet it. A legacy revision is never refused.
    """
    if revision.legacy:
        return None
    if not flows.ROLLBACK_SAFE:
        return ReadinessReason.ROLLBACK_NOT_SAFE
    if revision.dispatch is DispatchKind.CONTACT_CENTER and not flows.CONTACT_CENTER_FLOWS_READY:
        return ReadinessReason.CONTACT_CENTER_NOT_READY
    return None


def readiness(
    revision: FlowRevision,
    pool: CapacityPool | None,
    *,
    offered_subjects: Collection[str],
    coded_skills: Collection[int] = (),
    carrier: ContactCenterCapabilities | None = None,
) -> tuple[ReadinessItem, ...]:
    """Return the seven checklist items, in order, for activating ``revision``.

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
        _item(ReadinessCheck.ROLLBACK, _rollback_reason(revision)),
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
