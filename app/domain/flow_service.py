"""Flow use cases: the flows of a service, their drafts and their lifecycle (ADR 0121 step 2d).

A flow belongs to a service (a group). An administrator edits its *draft* revision, publishes it,
and asks for activation, which the readiness checklist refuses until every point is in order
(``app.domain.flow_readiness.readiness``). Every operation asks the matrix first
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
    subject_page     the extra skills of one offered subject of the draft
    save_subject_skills  write them into the draft
    discard          throw the draft away; the published revision stays
    activate         refused unless the checklist of the current revision is all in order
    archive          an active flow takes no new requests; the ones it has keep it; its draft goes
    delete           a flow that was never active goes with all its revisions

What may be done with a flow is ``flow_readiness.flow_actions``: the use cases guard with it and
the pages show their buttons from it.
"""

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from app.domain import flow_readiness as readiness_rules
from app.domain import service_flows as flows
from app.domain.admin import AdminService
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.contact_center import capabilities_of
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.flow_readiness import (
    FlowActions,
    ReadinessItem,
)
from app.domain.lookups import group_summary_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.routing import Subject
from app.domain.scheduling import Group, GroupSummary
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
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
DELETED = "flow.deleted"
"""Details of the audit events (action ``group.edit``); the flow's code and a revision follow."""
RESERVED_CODES = frozenset({"new"})
"""Codes a flow may not have: they are the address of a page (``/flows/new``)."""
RESERVED_CODE_FIELD = "flow_code_reserved"
"""The field of the refusal of a reserved code, apart from ``flow_code`` (a wrong form)."""
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
        subjects: The codes of the offered subjects. The extra skills of each are not part of the
            form: ``save`` keeps what the stored draft has for a subject that stays offered, under
            the lock, so a save of the subject's own page in between is never lost.
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
    subjects: frozenset[str]
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
        return self.flow.listed


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
        pool: The pool of the revision the page is about, if any (an existing pool of another
            organisation is never returned).
        choices: What the form offers.
        items: The readiness checklist of the revision the page is about.
        activation: The checklist the activation request is judged on: the current revision's,
            ``None`` while the flow has no published revision.
        skills: Every skill of the organisation by id, archived ones too, for the read-only view.
        actions: What the lifecycle allows to do with the flow now.
        missing: The skills the revision ``items`` judge needs that its pool does not guarantee.
        activation_missing: The same for the revision ``activation`` judges.
    """

    summary: GroupSummary
    flow: ServiceFlow
    pool: CapacityPool | None
    choices: FlowChoices
    items: tuple[ReadinessItem, ...]
    activation: tuple[ReadinessItem, ...] | None
    skills: Mapping[int, Skill]
    actions: FlowActions
    missing: frozenset[int] = frozenset()
    activation_missing: frozenset[int] = frozenset()

    @property
    def judges_draft(self) -> bool:
        """Whether ``items`` judge the draft (there is one), else the published revision."""
        return self.flow.draft is not None

    @property
    def activation_revision(self) -> int | None:
        """The number of the revision activation is judged on, ``None`` before the first."""
        return self.flow.current.revision if self.flow.current else None


@dataclass(frozen=True)
class SubjectSkillsPage:
    """The page that chooses the extra skills of one offered subject of a flow's draft.

    Attributes:
        summary: The service.
        flow: The flow.
        subject: The subject.
        skills: The organisation's skills not archived, and the archived ones the subject holds.
        chosen: The extra skills the subject has in the draft now.
    """

    summary: GroupSummary
    flow: ServiceFlow
    subject: Subject
    skills: tuple[Skill, ...]
    chosen: frozenset[int]


@dataclass(frozen=True)
class _Held:
    """A flow found and locked inside one transaction (``FlowService._locked_flow``)."""

    tx: Transaction
    scope: Scope
    summary: GroupSummary
    flow: ServiceFlow


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


def _revision(
    flow_id: int | None,
    number: int,
    draft: FlowDraftInput,
    extras: Mapping[str, frozenset[int]],
) -> FlowRevision:
    """Return the unpublished revision ``number`` holding ``draft``.

    ``extras`` are the extra skills the revision it replaces has per subject; a subject that is
    newly offered starts with none, and one that is not offered is not carried.
    """
    return FlowRevision(
        flow_id=flow_id,
        revision=number,
        labels=dict(draft.labels),
        intake_modes=draft.intake_modes,
        interaction_kind=draft.interaction_kind,
        duration_minutes=draft.duration_minutes,
        capacity_pool_id=draft.capacity_pool_id,
        required_skill_ids=draft.required_skill_ids,
        subject_skill_ids={code: extras.get(code, frozenset()) for code in sorted(draft.subjects)},
        dispatch=draft.dispatch,
        handling_action=draft.handling_action,
        completion=draft.completion,
        collects_email=draft.collects_email,
    )


def _counted_flows(stored: list[ServiceFlow]) -> int:
    """Return how many of the service's flows count against the limit (not archived, not legacy)."""
    return sum(1 for flow in stored if flow.state is not FlowState.ARCHIVED and not flow.legacy)


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
                ``invalid_configuration`` (``flow_code``, ``flow_code_reserved``, ``flow_limit``,
                what ``check_draft_content`` and ``check_flow_references`` name), ``slug_taken``
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
                raise _invalid(RESERVED_CODE_FIELD)
            flow = ServiceFlow(
                None,
                group.id,
                code.strip(),
                FlowState.DRAFT,
                draft.default_for,
                None,
                _revision(None, 1, draft, {}),
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
        async with self._locked_flow(principal, slug, code) as held:
            stored = held.flow
            if stored.draft is None or not readiness_rules.flow_actions(stored, None).save:
                raise _invalid("state")
            default_for = stored.default_for if stored.defaults_fixed else draft.default_for
            changed = replace(
                stored,
                default_for=default_for,
                draft=_revision(
                    stored.id, stored.draft.revision, draft, stored.draft.subject_skill_ids
                ),
            )
            written = await held.tx.save_draft(held.scope, changed)
            await self._record(
                held.tx, principal, held.summary, DRAFT_SAVED, written, written.draft
            )
        return written

    async def prepare(self, principal: Principal, slug: str, code: str) -> ServiceFlow:
        """Start a new revision: a draft copied from the published one, for a flow with none.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``configuration_object_missing`` (``flow_code``) for an unknown or legacy flow
                and ``invalid_configuration`` (``state``) for an archived flow, or one that has
                a draft already or no published revision.
        """
        async with self._locked_flow(principal, slug, code) as held:
            stored = held.flow
            current = stored.current
            if current is None or not readiness_rules.flow_actions(stored, None).prepare:
                raise _invalid("state")
            copy = replace(
                current, revision=current.revision + 1, published_at=None, published_by=None
            )
            written = await held.tx.save_draft(held.scope, replace(stored, draft=copy))
            await self._record(
                held.tx, principal, held.summary, DRAFT_SAVED, written, written.draft
            )
        return written

    async def publish(self, principal: Principal, slug: str, code: str) -> ServiceFlow:
        """Publish the flow's draft: it becomes the current revision. The flow is not activated.

        The draft is judged by the readiness checklist first and refused unless every point is in
        order, except the points that follow (``flow_readiness.can_publish``).

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``staff_not_found`` (the administrator has no staff record),
                ``configuration_object_missing`` (``flow_code``, or ``draft`` without one),
                ``invalid_configuration`` (``default_for``, ``state`` for an archived flow,
                ``readiness`` for a draft whose checklist is not in order, and what
                ``check_flow_references`` names).
        """
        async with self._locked_flow(principal, slug, code) as held:
            tx, stored = held.tx, held.flow
            draft = stored.draft
            if draft is None or not readiness_rules.flow_actions(stored, None).save:
                raise _guard_error(stored)
            items = await _readiness_of(tx, held.scope, held.summary.group, draft)
            readiness_rules.check_publish_ready(items)
            actor = await tx.get_staff(principal.subject)
            if actor is None or actor.id is None:
                raise DomainError(ErrorCode.STAFF_NOT_FOUND)
            now = self._clock.now()
            published = await tx.publish_draft(held.scope, stored.id or 0, by=actor.id, at=now)
            await self._record(
                tx, principal, held.summary, PUBLISHED, published, published.current, now
            )
        return published

    async def discard(self, principal: Principal, slug: str, code: str) -> None:
        """Throw away the draft of a flow that has a published revision; that revision stays.

        A flow that was never published has nothing to fall back on: it is deleted (``delete``).

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``configuration_object_missing`` (``flow_code``, or ``draft`` without one) or
                ``invalid_configuration`` (``state``) for an archived flow or one never published.
        """
        async with self._locked_flow(principal, slug, code) as held:
            stored = held.flow
            if not readiness_rules.flow_actions(stored, None).discard:
                raise _guard_error(stored)
            await held.tx.discard_draft(held.scope, stored.id or 0)
            await self._record(
                held.tx, principal, held.summary, DRAFT_DISCARDED, stored, stored.draft
            )

    async def delete(self, principal: Principal, slug: str, code: str) -> None:
        """Delete a flow that was never active, with all its revisions.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``configuration_object_missing`` (``flow_code``), ``invalid_configuration``
                (``state``) for a flow that is or was active, ``configuration_object_in_use``
                (``flow_id``) for one a booking refers to.
        """
        async with self._locked_flow(principal, slug, code) as held:
            stored = held.flow
            if not readiness_rules.flow_actions(stored, None).delete:
                raise _invalid("state")
            await held.tx.delete_service_flow(held.scope, stored.id or 0)
            await self._record(held.tx, principal, held.summary, DELETED, stored)

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
        async with self._locked_flow(principal, slug, code) as held:
            tx, stored = held.tx, held.flow
            current = stored.current
            if stored.state is not FlowState.DRAFT or current is None:
                raise _invalid("state")
            items = await _readiness_of(tx, held.scope, held.summary.group, current)
            if not readiness_rules.flow_actions(stored, items).activate:
                return Activation(False, items)
            active = await tx.set_flow_state(held.scope, stored.id or 0, FlowState.ACTIVE)
            await self._record(tx, principal, held.summary, ACTIVATED, active, active.current)
        return Activation(True, items)

    async def archive(self, principal: Principal, slug: str, code: str) -> ServiceFlow:
        """Archive an active flow: no new request goes through it.

        Existing requests keep it, and the portal cannot activate it again. A draft the flow still
        has is discarded in the same transaction (a second audit event), because an archived flow
        is history.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived``,
                ``configuration_object_missing`` (``flow_code``) or ``invalid_configuration``
                (``state``) for a flow that is not active (one never active is deleted).
        """
        async with self._locked_flow(principal, slug, code) as held:
            tx, stored = held.tx, held.flow
            if not readiness_rules.flow_actions(stored, None).archive:
                raise _invalid("state")
            if stored.draft is not None:
                await tx.discard_draft(held.scope, stored.id or 0)
                await self._record(
                    tx, principal, held.summary, DRAFT_DISCARDED, stored, stored.draft
                )
            archived = await tx.set_flow_state(held.scope, stored.id or 0, FlowState.ARCHIVED)
            await self._record(tx, principal, held.summary, ARCHIVED, archived, archived.current)
        return archived

    async def subject_page(
        self, principal: Principal, slug: str, code: str, subject: str
    ) -> SubjectSkillsPage:
        """Return the page of the extra skills of ``subject`` in the flow's draft.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or
                ``configuration_object_missing`` (``flow_code``, or ``subject`` for a subject the
                draft does not offer, a flow without a draft and an archived flow).
        """
        summary = await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.read() as tx:
            flow = await _own_flow(tx, scope, summary.group, code)
            found = _offered(flow, summary.group, subject)
            chosen = flow.working.subject_skill_ids[subject]
            skills = await organisation_skills(
                tx, scope, summary.group.organisation_id, held=chosen
            )
        return SubjectSkillsPage(summary, flow, found, tuple(skills), chosen)

    async def save_subject_skills(
        self, principal: Principal, slug: str, code: str, subject: str, skill_ids: frozenset[int]
    ) -> ServiceFlow:
        """Write the extra skills of one offered subject into the flow's draft.

        Raises:
            DomainError: As ``subject_page``, plus ``configuration_object_missing`` and
                ``configuration_out_of_scope`` (``required_skill_ids``) or
                ``invalid_configuration`` for a skill that cannot be chosen.
        """
        async with self._locked_flow(principal, slug, code) as held:
            _offered(held.flow, held.summary.group, subject)
            draft = cast(FlowRevision, held.flow.draft)  # _offered found a draft to edit
            extras = {**draft.subject_skill_ids, subject: skill_ids}
            changed = replace(held.flow, draft=replace(draft, subject_skill_ids=extras))
            written = await held.tx.save_draft(held.scope, changed)
            await self._record(
                held.tx, principal, held.summary, DRAFT_SAVED, written, written.draft
            )
        return written

    @asynccontextmanager
    async def _locked_flow(
        self, principal: Principal, slug: str, code: str
    ) -> AsyncIterator[_Held]:
        """Authorise, open the locked transaction and find the service's flow ``code`` in it."""
        await self._admin.authorize_group(principal, _PERMISSION, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            summary = await group_summary_by_slug(tx, slug)
            yield _Held(tx, scope, summary, await _own_flow(tx, scope, summary.group, code))

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
        await self._admin.record_change(
            tx,
            at or self._clock.now(),
            principal,
            _PERMISSION,
            summary.organisation_slug,
            summary.group.slug,
            detail,
        )


def _guard_error(flow: ServiceFlow) -> DomainError:
    """Return why a draft action is refused: no draft, or the flow's state does not allow it."""
    if flow.draft is None and flow.state is not FlowState.ARCHIVED:
        return missing_object("draft")
    return _invalid("state")


def _offered(flow: ServiceFlow, group: Group, subject: str) -> Subject:
    """Return the service's subject when the flow's draft can be edited and offers it.

    A subject the service no longer has is not offered, whatever the draft still lists.

    Raises:
        DomainError: ``configuration_object_missing`` (``subject``) otherwise: there is nothing to
            edit there, and the answer tells nothing more.
    """
    draft = flow.draft
    found = next((s for s in group.routing.subjects if s.code == subject), None)
    if (
        draft is None
        or found is None
        or not readiness_rules.flow_actions(flow, None).save
        or subject not in draft.subject_skill_ids
    ):
        raise missing_object("subject")
    return found


def _line(
    flow: ServiceFlow, pools: Mapping[int | None, CapacityPool], skills: Mapping[int | None, Skill]
) -> FlowLine:
    """Return ``flow`` with the pool and the skills of the revision the list shows."""
    revision = flow.listed
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
            if flow.legacy:
                break
            return flow
    raise missing_object("flow_code")


async def _pool_of(tx: Transaction, scope: Scope, revision: FlowRevision) -> CapacityPool | None:
    """Return the pool ``revision`` draws on, if it is inside ``scope``."""
    if revision.capacity_pool_id is None:
        return None
    return await tx.get_capacity_pool(scope, revision.capacity_pool_id)


async def _readiness_of(
    tx: Transaction, scope: Scope, group: Group, revision: FlowRevision
) -> tuple[ReadinessItem, ...]:
    """Return the readiness checklist of ``revision`` against what the organisation has now.

    That is its pool, the service's subjects, what the platform of the service's contact-center
    routing carries (ADR 0121 step 6) and, only for a contact-center revision that requires
    skills, which skills have a contact-center code.
    """
    coded: frozenset[int] = frozenset()
    if revision.dispatch is flows.DispatchKind.CONTACT_CENTER and revision.all_skill_ids:
        coded = frozenset(
            s.id
            for s in await all_skills(tx, scope, group.organisation_id)
            if s.id is not None and s.contact_center_code is not None
        )
    return readiness_rules.readiness(
        revision,
        await _pool_of(tx, scope, revision),
        offered_subjects=[s.code for s in group.routing.subjects],
        coded_skills=coded,
        carrier=capabilities_of(group.contact_center),
    )


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
    shown = flow.working
    activation = (
        None if flow.current is None else await _readiness_of(tx, scope, group, flow.current)
    )
    items = await _readiness_of(tx, scope, group, shown)
    pool = await _pool_of(tx, scope, shown)
    current_pool = pool if flow.current is shown else None
    if flow.current is not None and current_pool is None:
        current_pool = await _pool_of(tx, scope, flow.current)
    return FlowPage(
        summary,
        flow,
        pool,
        await _choices(tx, scope, group, shown),
        items,
        activation,
        {s.id: s for s in await all_skills(tx, scope, group.organisation_id) if s.id is not None},
        readiness_rules.flow_actions(flow, activation, items if flow.draft is not None else None),
        readiness_rules.missing_guarantees(shown, pool),
        readiness_rules.missing_guarantees(flow.current, current_pool)
        if flow.current
        else frozenset(),
    )
