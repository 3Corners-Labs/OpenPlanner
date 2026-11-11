"""Flow use cases: the flows of a service, their drafts and their lifecycle (ADR 0121 step 2d).

A flow belongs to a service (a group). An administrator edits its *draft* revision, publishes it,
and asks for activation, which the readiness checklist refuses until every point is in order
(``app.domain.service_flows.readiness``). Every operation asks the matrix first
(``AdminService.authorize_group``, permission ``group.edit``, the one permission of the flows
sub-tab) and every change runs in one locked transaction with its audit event, carrying the flow's
code and a revision number, never a label. The wrapper flows of a pre-flow service (*legacy*) are
listed and never changed here: every operation on one answers like an unknown flow.

    overview         the service's flows with their pool and required skills
    page             one flow, its draft or published revision, its checklist and what to choose
    form             what the form of a new flow offers to choose from
    create           a draft flow by code; at most ``max_flows_per_service`` not archived
    save             the draft revision of a flow
    prepare          a draft copied from the published revision of a flow that has none
    publish          the draft becomes the current revision (publishing does not activate)
    discard          delete the draft; a flow never published disappears
    activate         refused unless the checklist of the current revision is all in order
    archive          an active flow takes no new requests; the ones it has keep it
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from app.domain import service_flows as flows
from app.domain.admin import AdminService, record_change
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.lookups import group_summary_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.routing import Subject, validate_labels
from app.domain.scheduling import Group, GroupSummary, valid_slot_minutes
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ReadinessItem,
    ServiceFlow,
)
from app.domain.skill_service import all_skills, organisation_skills
from app.domain.skills import Skill
from app.domain.tunable import value_of

_PERMISSION = Permission.GROUP_EDIT
CREATED = "flow.created"
DRAFT_SAVED = "flow.draft_saved"
PUBLISHED = "flow.published"
DRAFT_DISCARDED = "flow.draft_discarded"
ACTIVATED = "flow.activated"
ARCHIVED = "flow.archived"
"""Details of the audit events (action ``group.edit``); the flow's code and a revision follow."""
RESERVED_CODES = frozenset({"new"})
"""Codes a flow may not have: they are the address of a page (``/flows/new``)."""
LIMIT_FIELD = "flow_limit"
"""The field of the refusal when the service has as many flows as the platform allows."""


@dataclass(frozen=True)
class FlowDraftInput:
    """What the editor of a flow submits for its draft revision.

    Every field may be left unset: a draft is allowed to be incomplete, and the readiness
    checklist says what is missing. Only a value that is wrong in itself is refused.

    Attributes:
        labels: The flow's name per locale (``nl`` and ``en``).
        intake_modes: The entry paths the flow offers.
        interaction_kind: The contact channel, ``None`` when not chosen yet.
        duration_minutes: The appointment length, ``None`` when not chosen yet.
        capacity_pool_id: The counted pool the flow draws on.
        required_skill_ids: Skills every handler needs.
        subject_skill_ids: The offered subjects with the extra skills each needs.
        dispatch: Where requests go to staff.
        handling_action: What staff do with a request.
        completion: What marks a request as done.
        collects_email: Whether the form asks for an email address.
        default_for: The entry paths the flow is the default of (a flow never published only).
    """

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
    collects_email: bool
    default_for: frozenset[IntakeMode]


@dataclass(frozen=True)
class FlowLine:
    """One flow of the list with the pool and skills of the revision it shows.

    Attributes:
        flow: The flow.
        pool: The pool of the shown revision (the current one, else the draft), if any.
        skills: The skills the flow itself requires, by code.
    """

    flow: ServiceFlow
    pool: CapacityPool | None
    skills: tuple[Skill, ...]

    @property
    def revision(self) -> FlowRevision:
        """The revision the list shows: the published one, else the draft."""
        shown = self.flow.current or self.flow.draft
        if shown is None:  # pragma: no cover - a flow always has one
            raise missing_object("revision")
        return shown

    @property
    def legacy(self) -> bool:
        """Whether this is the wrapper of a pre-flow service: shown, never changed here."""
        return self.flow.current is not None and self.flow.current.legacy


@dataclass(frozen=True)
class FlowOverview:
    """A service's flows, the archived ones or the others.

    Attributes:
        summary: The service.
        lines: The flows by code.
        archived: Whether the archived flows are listed instead of the others.
        can_add: Whether another flow fits under the platform's limit.
        limit: That limit: flows not archived per service.
    """

    summary: GroupSummary
    lines: tuple[FlowLine, ...]
    archived: bool
    can_add: bool
    limit: int


@dataclass(frozen=True)
class FlowChoices:
    """What the editor offers to choose from.

    Attributes:
        pools: The organisation's counted pools not archived, and the one the shown revision
            draws on when that is archived (kept, never newly chosen).
        skills: The organisation's skills not archived, and archived ones the revision names.
        subjects: The service's subjects, in their order.
    """

    pools: tuple[CapacityPool, ...]
    skills: tuple[Skill, ...]
    subjects: tuple[Subject, ...]


@dataclass(frozen=True)
class FlowForm:
    """What the form of a new flow needs."""

    summary: GroupSummary
    choices: FlowChoices


@dataclass(frozen=True)
class FlowPage:
    """One flow for its page.

    Attributes:
        summary: The service.
        flow: The flow.
        shown: The revision the page is about: the draft when there is one, else the current.
        pool: The pool of ``shown``, if any (an existing pool of another organisation is never
            returned).
        choices: What the form offers.
        items: The readiness checklist of ``shown``.
        activation: The checklist the activation request is judged on: the current revision's,
            ``None`` while the flow has no published revision.
        skills: Every skill of the organisation by id, archived ones too, for the read-only view.
    """

    summary: GroupSummary
    flow: ServiceFlow
    shown: FlowRevision
    pool: CapacityPool | None
    choices: FlowChoices
    items: tuple[ReadinessItem, ...]
    activation: tuple[ReadinessItem, ...] | None
    skills: Mapping[int, Skill]

    @property
    def editable(self) -> bool:
        """Whether the page edits a draft (not archived, and there is one)."""
        return self.flow.draft is not None and self.flow.state is not FlowState.ARCHIVED

    @property
    def can_activate(self) -> bool:
        """Whether the activation request would be accepted now."""
        return (
            self.flow.state is FlowState.DRAFT
            and self.activation is not None
            and flows.can_activate(self.activation)
        )


@dataclass(frozen=True)
class Activation:
    """The outcome of a request to activate a flow.

    Attributes:
        activated: Whether the flow is active now.
        items: The checklist of the current revision the decision was made on.
    """

    activated: bool
    items: tuple[ReadinessItem, ...]


def _invalid(field: str) -> DomainError:
    """Return the configuration error for ``field``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


def check_draft_input(draft: FlowDraftInput) -> None:
    """Refuse a draft that is wrong in itself; one that is only incomplete passes.

    The labels need both locales, the duration is an allowed appointment length, and the
    dispatch, completion and action are among what ADR 0121 has built so far (the ``BUILT_*``
    constants, read when called) and, for the action, allowed for the contact channel.

    Raises:
        DomainError: ``invalid_configuration`` with field ``labels``, ``duration_minutes``,
            ``dispatch``, ``completion`` or ``handling_action``.
    """
    validate_labels(draft.labels, "labels")
    if draft.duration_minutes is not None and not valid_slot_minutes(draft.duration_minutes):
        raise _invalid("duration_minutes")
    if draft.dispatch is not None and draft.dispatch not in flows.BUILT_DISPATCH:
        raise _invalid("dispatch")
    if draft.completion is not None and draft.completion not in flows.BUILT_COMPLETION:
        raise _invalid("completion")
    action = draft.handling_action
    if action is not None:
        kind = draft.interaction_kind
        if kind is None or action not in flows.COMBINATIONS[kind].actions:
            raise _invalid("handling_action")
        if action not in flows.BUILT_ACTIONS:
            raise _invalid("handling_action")


def _revision(flow_id: int | None, number: int, draft: FlowDraftInput) -> FlowRevision:
    """Return the unpublished revision ``number`` holding ``draft``."""
    return FlowRevision(
        flow_id=flow_id,
        revision=number,
        labels=dict(draft.labels),
        intake_modes=draft.intake_modes,
        interaction_kind=draft.interaction_kind,
        duration_minutes=draft.duration_minutes,
        capacity_pool_id=draft.capacity_pool_id,
        required_skill_ids=draft.required_skill_ids,
        subject_skill_ids={code: frozenset(ids) for code, ids in draft.subject_skill_ids.items()},
        dispatch=draft.dispatch,
        handling_action=draft.handling_action,
        completion=draft.completion,
        collects_email=draft.collects_email,
    )


def _counted_flows(stored: list[ServiceFlow]) -> int:
    """Return how many of the service's flows count against the limit (not archived, not legacy)."""
    return sum(
        1
        for flow in stored
        if flow.state is not FlowState.ARCHIVED and not (flow.current and flow.current.legacy)
    )


class FlowService:
    """Application service for the flows of a service.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events and publishing.
        admin: Authorises changes and audits refusals.
        max_flows: The platform's limit of flows not archived per service (class C,
            ``max_flows_per_service``), or the live setting that holds it.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        max_flows: int | Callable[[], int],
    ) -> None:
        """Store the collaborators and the limit."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._max = max_flows

    @property
    def max_flows(self) -> int:
        """The platform's limit of flows not archived per service."""
        return value_of(self._max)

    async def overview(
        self, principal: Principal, slug: str, *, archived: bool = False
    ) -> FlowOverview:
        """Return the service's flows (the archived ones on request) with their pool and skills.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        summary = await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        group = summary.group
        async with self._uow.read() as tx:
            stored = await tx.list_service_flows(scope, group.id)
            pools = {
                p.id: p
                for archived_too in (False, True)
                for p in await tx.list_capacity_pools(
                    scope, group.organisation_id, archived=archived_too
                )
            }
            skills = {s.id: s for s in await all_skills(tx, scope, group.organisation_id)}
        shown = [f for f in stored if (f.state is FlowState.ARCHIVED) is archived]
        lines = tuple(_line(flow, pools, skills) for flow in sorted(shown, key=lambda f: f.code))
        return FlowOverview(
            summary,
            lines,
            archived,
            _counted_flows(stored) < self.max_flows,
            self.max_flows,
        )

    async def form(self, principal: Principal, slug: str) -> FlowForm:
        """Return what the form of a new flow offers: counted pools, skills and subjects.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        summary = await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.read() as tx:
            choices = await _choices(tx, scope, summary.group, None)
        return FlowForm(summary, choices)

    async def page(self, principal: Principal, slug: str, code: str) -> FlowPage:
        """Return the flow ``code`` of the service for its page.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or
                ``configuration_object_missing`` (field ``flow_code``) for a flow the service
                does not have and for a legacy flow.
        """
        summary = await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        group = summary.group
        async with self._uow.read() as tx:
            flow = await _own_flow(tx, scope, group, code)
            return await _page(tx, scope, summary, flow)

    async def create(
        self, principal: Principal, slug: str, code: str, draft: FlowDraftInput
    ) -> ServiceFlow:
        """Create a flow ``code`` in state draft with ``draft`` as its first revision.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``invalid_configuration`` (``flow_code``, ``flow_limit``, what
                ``check_draft_input`` and ``check_flow_references`` name), ``slug_taken``
                (``flow_code``) or, for ``capacity_pool_id`` and ``required_skill_ids``,
                ``configuration_object_missing`` and ``configuration_out_of_scope``.
        """
        await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            summary = await group_summary_by_slug(tx, slug)
            group = summary.group
            if _counted_flows(await tx.list_service_flows(scope, group.id)) >= self.max_flows:
                raise _invalid(LIMIT_FIELD)
            if code.strip() in RESERVED_CODES:
                raise _invalid("flow_code")
            check_draft_input(draft)
            await _check_pool(tx, scope, draft.capacity_pool_id)
            flow = ServiceFlow(
                None,
                group.id,
                code.strip(),
                FlowState.DRAFT,
                draft.default_for,
                None,
                _revision(None, 1, draft),
            )
            stored = await tx.save_draft(scope, flow)
            await self._record(tx, principal, summary, CREATED, stored, stored.draft)
        return stored

    async def save(
        self, principal: Principal, slug: str, code: str, draft: FlowDraftInput
    ) -> ServiceFlow:
        """Write ``draft`` over the flow's draft revision.

        A flow that has a published revision keeps its code and default entry paths; whatever
        ``draft.default_for`` says is ignored then.

        Raises:
            DomainError: As ``create`` (without the limit and ``slug_taken``), plus
                ``configuration_object_missing`` (``flow_code``) for an unknown or legacy flow,
                ``invalid_configuration`` (``state``) for an archived one or one without a draft.
        """
        await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            summary = await group_summary_by_slug(tx, slug)
            stored = await _own_flow(tx, scope, summary.group, code)
            if stored.state is FlowState.ARCHIVED or stored.draft is None:
                raise _invalid("state")
            check_draft_input(draft)
            await _check_pool(tx, scope, draft.capacity_pool_id)
            default_for = stored.default_for if stored.current else draft.default_for
            changed = ServiceFlow(
                stored.id,
                stored.group_id,
                stored.code,
                stored.state,
                default_for,
                stored.current,
                _revision(stored.id, stored.draft.revision, draft),
            )
            written = await tx.save_draft(scope, changed)
            await self._record(tx, principal, summary, DRAFT_SAVED, written, written.draft)
        return written

    async def prepare(self, principal: Principal, slug: str, code: str) -> ServiceFlow:
        """Start a new revision: a draft copied from the published one, for a flow with none.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``configuration_object_missing`` (``flow_code``) for an unknown or legacy flow
                and ``invalid_configuration`` (``state``) for an archived flow, or one that has
                a draft already or no published revision.
        """
        await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            summary = await group_summary_by_slug(tx, slug)
            stored = await _own_flow(tx, scope, summary.group, code)
            current = stored.current
            if stored.state is FlowState.ARCHIVED or stored.draft is not None or current is None:
                raise _invalid("state")
            copy = replace(
                current, revision=current.revision + 1, published_at=None, published_by=None
            )
            written = await tx.save_draft(scope, replace(stored, draft=copy))
            await self._record(tx, principal, summary, DRAFT_SAVED, written, written.draft)
        return written

    async def publish(self, principal: Principal, slug: str, code: str) -> ServiceFlow:
        """Publish the flow's draft: it becomes the current revision. The flow is not activated.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``staff_not_found`` (the administrator has no staff record),
                ``configuration_object_missing`` (``flow_code``, or ``draft`` without one),
                ``invalid_configuration`` (``default_for``, ``state`` for an archived flow, and
                what ``check_flow_references`` names).
        """
        await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            summary = await group_summary_by_slug(tx, slug)
            stored = await _own_flow(tx, scope, summary.group, code)
            if stored.state is FlowState.ARCHIVED:
                raise _invalid("state")
            actor = await tx.get_staff(principal.subject)
            if actor is None or actor.id is None:
                raise DomainError(ErrorCode.STAFF_NOT_FOUND)
            now = self._clock.now()
            published = await tx.publish_draft(scope, stored.id or 0, by=actor.id, at=now)
            await self._record(tx, principal, summary, PUBLISHED, published, published.current, now)
        return published

    async def discard(self, principal: Principal, slug: str, code: str) -> bool:
        """Delete the flow's draft; return whether the flow itself is gone (never published).

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived`` or
                ``configuration_object_missing`` (``flow_code``, or ``draft`` without one).
        """
        await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            summary = await group_summary_by_slug(tx, slug)
            stored = await _own_flow(tx, scope, summary.group, code)
            await tx.discard_draft(scope, stored.id or 0)
            await self._record(tx, principal, summary, DRAFT_DISCARDED, stored, stored.draft)
        return stored.current is None

    async def activate(self, principal: Principal, slug: str, code: str) -> Activation:
        """Activate a flow in state draft, unless its current revision is not ready.

        The checklist is computed for the *current* revision, not the draft. While one point is
        missing (and today ``RETENTION_BUILT`` keeps one missing for every non-legacy flow) the
        outcome says so and nothing changes.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``configuration_object_missing`` (``flow_code``), ``invalid_configuration``
                (``state``: not in state draft, or no published revision) and
                ``configuration_object_exists`` (``default_for``: another active flow is the
                default of the path).
        """
        await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            summary = await group_summary_by_slug(tx, slug)
            stored = await _own_flow(tx, scope, summary.group, code)
            current = stored.current
            if stored.state is not FlowState.DRAFT or current is None:
                raise _invalid("state")
            pool = await _pool_of(tx, scope, current)
            subjects = [s.code for s in summary.group.routing.subjects]
            items = flows.readiness(current, pool, offered_subjects=subjects)
            if not flows.can_activate(items):
                return Activation(False, items)
            active = await tx.set_flow_state(scope, stored.id or 0, FlowState.ACTIVE)
            await self._record(tx, principal, summary, ACTIVATED, active, active.current)
        return Activation(True, items)

    async def archive(self, principal: Principal, slug: str, code: str) -> ServiceFlow:
        """Archive an active flow: no new request goes through it; existing ones keep it.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``configuration_object_missing`` (``flow_code``) or ``invalid_configuration``
                (``state``) for a flow that is not active (a flow in state draft is removed by
                discarding its draft).
        """
        await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            summary = await group_summary_by_slug(tx, slug)
            stored = await _own_flow(tx, scope, summary.group, code)
            flows.check_state_change(stored, FlowState.ARCHIVED)
            archived = await tx.set_flow_state(scope, stored.id or 0, FlowState.ARCHIVED)
            await self._record(tx, principal, summary, ARCHIVED, archived, archived.current)
        return archived

    async def _record(
        self,
        tx: Transaction,
        principal: Principal,
        summary: GroupSummary,
        action: str,
        flow: ServiceFlow,
        revision: FlowRevision | None = None,
        at: datetime | None = None,
    ) -> None:
        """Audit a change of a flow in the transaction that made it (the code and a number)."""
        detail = f"{action},flow:{flow.code}"
        if revision is not None:
            detail += f",revision:{revision.revision}"
        await record_change(
            tx,
            at or self._clock.now(),
            principal,
            _PERMISSION,
            summary.organisation_slug,
            summary.group.slug,
            detail,
        )


def _line(
    flow: ServiceFlow, pools: Mapping[int | None, CapacityPool], skills: Mapping[int | None, Skill]
) -> FlowLine:
    """Return ``flow`` with the pool and the skills of the revision the list shows."""
    revision = flow.current or flow.draft
    if revision is None:  # pragma: no cover - a flow always has one
        raise missing_object("revision")
    required = sorted(
        (skills[i] for i in revision.required_skill_ids if i in skills), key=lambda s: s.code
    )
    return FlowLine(flow, pools.get(revision.capacity_pool_id), tuple(required))


async def _own_flow(tx: Transaction, scope: Scope, group: Group, code: str) -> ServiceFlow:
    """Return the service's flow ``code``; unknown, another service's and a legacy flow are missing.

    A flow of another service or organisation is not in the service's list, and the list is
    filtered by ``scope`` in SQL, so a code tells nothing about what exists elsewhere (A05). A
    legacy flow follows the service's own settings and has no page and no change here.
    """
    for flow in await tx.list_service_flows(scope, group.id):
        if flow.code == code:
            if flow.current is not None and flow.current.legacy:
                break
            return flow
    raise missing_object("flow_code")


async def _pool_of(tx: Transaction, scope: Scope, revision: FlowRevision) -> CapacityPool | None:
    """Return the pool ``revision`` draws on, if it is inside ``scope``."""
    if revision.capacity_pool_id is None:
        return None
    return await tx.get_capacity_pool(scope, revision.capacity_pool_id)


async def _check_pool(tx: Transaction, scope: Scope, pool_id: int | None) -> None:
    """Refuse a pool that is not a counted one: a flow does not draw on a service's own staffing.

    Whether the pool exists, belongs to the organisation and is not archived is for
    ``check_flow_references``, which the adapter runs; the legacy pool is the one thing it lets
    through, because the wrapper flows are written with it.
    """
    if pool_id is None:
        return
    pool = await tx.get_capacity_pool(scope, pool_id)
    if pool is not None and pool.mode is not PoolMode.COUNTED:
        raise _invalid("capacity_pool_id")


async def _choices(
    tx: Transaction, scope: Scope, group: Group, shown: FlowRevision | None
) -> FlowChoices:
    """Return what the editor offers: counted pools, skills and subjects of the service."""
    organisation_id = group.organisation_id
    pools = [
        p
        for p in await tx.list_capacity_pools(scope, organisation_id)
        if p.mode is PoolMode.COUNTED
    ]
    own = shown.capacity_pool_id if shown is not None else None
    if own is not None and all(p.id != own for p in pools):
        kept = await tx.get_capacity_pool(scope, own)
        if kept is not None and kept.mode is PoolMode.COUNTED:
            pools.append(kept)
    held = shown.all_skill_ids if shown is not None else frozenset[int]()
    skills = await organisation_skills(tx, scope, organisation_id, held=held)
    return FlowChoices(
        tuple(sorted(pools, key=lambda p: (p.name, p.id or 0))),
        tuple(skills),
        group.routing.subjects,
    )


async def _page(
    tx: Transaction, scope: Scope, summary: GroupSummary, flow: ServiceFlow
) -> FlowPage:
    """Return the page of ``flow`` read inside ``tx``."""
    group = summary.group
    shown = flow.draft or flow.current
    if shown is None:  # pragma: no cover - a flow always has one
        raise missing_object("revision")
    offered = [s.code for s in group.routing.subjects]
    pool = await _pool_of(tx, scope, shown)
    activation = (
        flows.readiness(
            flow.current, await _pool_of(tx, scope, flow.current), offered_subjects=offered
        )
        if flow.current is not None
        else None
    )
    return FlowPage(
        summary,
        flow,
        shown,
        pool,
        await _choices(tx, scope, group, shown),
        flows.readiness(shown, pool, offered_subjects=offered),
        activation,
        {s.id: s for s in await all_skills(tx, scope, group.organisation_id) if s.id is not None},
    )
