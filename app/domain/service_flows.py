"""Service flows: how one service is taken in, handled and completed (ADR 0121).

A flow belongs to a group (the service offering) and has numbered, immutable revisions. The
combination of interaction kind, readiness and handling action is a closed table
(``COMBINATIONS``, ADR 0121 point 4). A draft is activated only when the readiness checklist
is all green. Behaviour never depends on a label; labels are display text only.

A published flow is changed by a *draft* revision next to its current one (``ServiceFlow.draft``);
publishing the draft makes it the current revision and the old one stays for the bookings made
under it.

Until ADR 0121 step 6 is built, the checklist refuses every value of a non-legacy revision
that is outside the ``BUILT_*`` constants below (reason ``not_built_yet``). The watchdog and the
deletion exist since step 5b (``RETENTION_BUILT``); every non-legacy revision is still refused
by its own point while the rollback gate ``ROLLBACK_SAFE`` is shut (reason
``rollback_not_safe``, ADR 0122), so no new flow can be activated.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, cast

from app.domain.capacity import CapacityPool, PoolMode
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
COMPLETION_OF: Mapping[DispatchKind, CompletionPolicy] = {
    DispatchKind.OWN_QUEUE: CompletionPolicy.OPERATOR,
    DispatchKind.CONTACT_CENTER: CompletionPolicy.PROVIDER_EVENT,
}
"""What completes the work of a new flow, by its dispatch (ADR 0121 step 6b, choice E2): the
operator in the own queue, the platform's event at the contact center. The built dispatch kinds
and completions are this map's keys and values."""
BUILT_DISPATCH: frozenset[DispatchKind] = frozenset(COMPLETION_OF)
"""Dispatch kinds a new flow may use; the contact center since ADR 0121 step 6b."""
BUILT_COMPLETION: frozenset[CompletionPolicy] = frozenset(COMPLETION_OF.values())
"""Completion policies a new flow may use; the platform's event since ADR 0121 step 6b."""
BUILT_ACTIONS: frozenset[HandlingAction] = frozenset(
    {HandlingAction.OPEN_VIDEO, HandlingAction.VIEW_REQUEST, HandlingAction.CALL}
)
"""Handling actions a new flow may use; assigning a workstation needs locations, not built."""
RETENTION_BUILT = True
"""Whether the watchdog and deletion of ADR 0121 point 7 exist: true since step 5b (the round
``app.domain.request_watch``, the deletion of the request-level record).

Each later step of ADR 0121 widens exactly these five constants and nothing else here. A
legacy revision skips all five tests.
"""
ROLLBACK_SAFE: Final = True
"""The rollback gate of ADR 0122 (design section 5.2): may a non-legacy flow be activated?

A non-legacy flow writes pool slots (and later occupations and requests) that the previous
supported minor does not know; it would count their bookings in a service's legacy slot and
never respect the pool across services. So a flow may be activated only in an image whose
previous supported minor already holds the readers and the accounting of every record it writes.
This stays false until the release after the last reader-adding step, and until the upgrade
matrix has proven it: activate a flow, book on the pool, roll back to the previous minor, book
and claim there, and check that no unit was sold twice (A30). Setting it true is a decision,
and needs ADR 0121 step 7b too: with the gate open, a service whose on-demand default is a pool
flow is refused by the legacy direct call and is served only by the immediate intake pages.

Read when called, never copied (tests flip it with a monkeypatch): ``readiness`` reports
``rollback_not_safe`` while it is false (ADR 0122 step 5), and
``flow_resolution.pool_flows_bookable`` keeps every flow on a counted pool out of the booking
paths.
"""
CONTACT_CENTER_FLOWS_READY: Final = False
"""May a non-legacy flow that dispatches to the contact center be activated once the rollback
gate ``ROLLBACK_SAFE`` is open? Not until checklist items G13 to G15 are done
(``docs/service-flows-gate-checklist.md``, product owner 2026-11-14): the watchdog ends the task
at the platform when it expires or cancels an attempt (G13), the own queue shows work the
contact center holds as held (G14), and the test host showed that the platform's task events
carry the origin (G15).

A fixed rule, never a setting: it is a protection. The PR that closes G13 to G15 flips it to
true. Read when called, never copied (tests flip it with a monkeypatch): ``readiness`` reports
``contact_center_not_ready`` for a non-legacy contact-center flow while it is false and the
gate is open. A legacy flow (the wrapper of an existing service, ADR 0121 step 2e) and a flow on
the own queue are never refused by it.
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


def interaction_of(revision: FlowRevision | None, recorded: bool) -> InteractionKind:
    """Return the interaction kind of a booking made under ``revision`` (ADR 0121 step 6).

    A booking without a flow (``recorded`` false) is a video booking: it is dispatched with its
    meeting. A recorded revision is a published one, which always names its interaction kind.

    Raises:
        DomainError: ``integration_not_ready`` (``interaction_kind``) for a recorded flow whose
            revision is missing or names no interaction kind: the task is refused rather than
            sent as a video task by guess.
    """
    if not recorded:
        return InteractionKind.VIDEO
    if revision is None or revision.interaction_kind is None:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "interaction_kind")
    return revision.interaction_kind


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
