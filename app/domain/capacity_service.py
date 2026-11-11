"""Capacity pool use cases: whose time flows draw on and which skills a pool guarantees (ADR 0121).

A *legacy* pool wraps one service's present staffing and is only shown; a *counted* pool is
kept here: a name and the skills every counted unit holds. How many units work per time block
is not set here yet (it arrives with interval accounting). Every operation asks the matrix
first (``AdminService.scoped``); every change runs in one locked transaction with its audit
event, carrying the pool's number and never a name. Reading the pools is ``organisation.view``
(a legacy pool only for a reader who reaches its service); every change, and the pages that edit
one pool, are ``capacity.manage``.

    overview   organisation.view   the organisation's pools with the flows that draw on them
    pool       capacity.manage     one counted pool, not archived
    form       capacity.manage     a pool's form (``None``: a new one) with the skills to choose
    create     capacity.manage     a counted pool: name and guaranteed skills; at most
                                   ``max_capacity_pools_per_organisation`` counted, not archived
    update     capacity.manage     name and guaranteed skills of a counted pool
    archive    capacity.manage     refused while a flow draws on it
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.domain.admin import AdminService, record_change
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.capacity import CapacityPool, PoolFlows, PoolMode
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.organisations import organisation_by_slug, own_object
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Organisation
from app.domain.skill_service import all_skills, organisation_skills
from app.domain.skills import Skill
from app.domain.tunable import value_of

_PERMISSION = Permission.CAPACITY_MANAGE
CREATED = "capacity_pool.created"
CHANGED = "capacity_pool.changed"
ARCHIVED = "capacity_pool.archived"
"""Details of the audit events (action ``capacity.manage``); the labels are in the audit page."""
LIMIT_FIELD = "pool_limit"
"""The field of the refusal when the organisation has as many counted pools as allowed."""


@dataclass(frozen=True)
class FlowRef:
    """A flow that draws on a pool, as the pool's page names it.

    Attributes:
        labels: The flow's label per locale (its code when it has none).
        service: Name of the service (group) the flow belongs to.
    """

    labels: Mapping[str, str]
    service: str


@dataclass(frozen=True)
class PoolLine:
    """One pool with the skills it guarantees and the flows that draw on it.

    Attributes:
        pool: The pool.
        skills: The skills it guarantees, by code (an archived one is kept and shown).
        flows: The flows inside the viewer's scope that draw on it.
        hidden: How many more flows, of services outside the scope, draw on it.
    """

    pool: CapacityPool
    skills: tuple[Skill, ...]
    flows: tuple[FlowRef, ...]
    hidden: int

    @property
    def shared(self) -> bool:
        """Whether more than one flow draws on the pool."""
        return len(self.flows) + self.hidden > 1


@dataclass(frozen=True)
class PoolOverview:
    """An organisation's pools, the archived ones or those in use.

    Attributes:
        organisation: The organisation.
        lines: The pools by name; legacy pools only of services the viewer reaches.
        archived: Whether the archived pools are listed instead of the others.
        editable: Whether the viewer may change them (``capacity.manage`` in this organisation).
        can_add: Whether another counted pool fits under the platform's limit.
        limit: That limit: counted pools not archived per organisation.
    """

    organisation: Organisation
    lines: tuple[PoolLine, ...]
    archived: bool
    editable: bool
    can_add: bool
    limit: int


@dataclass(frozen=True)
class PoolForm:
    """What a pool's form needs: the pool (``None`` for a new one) and the skills to choose from."""

    organisation: Organisation
    line: PoolLine | None
    choices: tuple[Skill, ...]


def can_manage(principal: Principal, slug: str) -> bool:
    """Return whether ``principal`` holds ``capacity.manage`` in organisation ``slug``."""
    return scope_for(principal, _PERMISSION).contains(slug, None)


class CapacityService:
    """Application service for the capacity pools of an organisation.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises changes and audits refusals.
        max_pools: The platform's limit of counted pools not archived per organisation (class C,
            ``max_capacity_pools_per_organisation``), or the live setting that holds it.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        max_pools: int | Callable[[], int],
    ) -> None:
        """Store the collaborators and the limit."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._max = max_pools

    async def overview(
        self, principal: Principal, slug: str, *, archived: bool = False
    ) -> PoolOverview:
        """Return the organisation's pools (the archived ones on request).

        Raises:
            DomainError: ``not_permitted`` (``organisation.view``) or ``organisation_not_found``.
        """
        scope = await self._admin.scoped(principal, Permission.ORGANISATION_VIEW, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            pools = await tx.list_capacity_pools(scope, organisation.id, archived=archived)
            skills = await _skills_by_id(tx, scope, organisation.id)
            lines = [await _line(tx, scope, pool, skills) for pool in pools]
            counted = await _counted(tx, scope, organisation.id)
        return PoolOverview(
            organisation,
            tuple(lines),
            archived,
            can_manage(principal, slug),
            counted < value_of(self._max),
            value_of(self._max),
        )

    async def pool(self, principal: Principal, slug: str, pool_id: int) -> PoolLine:
        """Return one counted pool of the organisation, not archived.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``configuration_object_missing`` (also for another organisation's pool and for
                an archived one) or ``invalid_configuration`` (``mode``) for a legacy pool.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            pool = await _own_editable_pool(tx, scope, organisation, pool_id)
            return await _line(tx, scope, pool, await _skills_by_id(tx, scope, organisation.id))

    async def form(self, principal: Principal, slug: str, pool_id: int | None) -> PoolForm:
        """Return what the form of a new pool (``None``) or of ``pool_id`` shows.

        The choices are the skills not archived, and an archived one the pool still guarantees.

        Raises:
            DomainError: As ``pool``.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            skills = await _skills_by_id(tx, scope, organisation.id)
            line = None
            if pool_id is not None:
                pool = await _own_editable_pool(tx, scope, organisation, pool_id)
                line = await _line(tx, scope, pool, skills)
            kept = line.pool.guaranteed_skill_ids if line else frozenset[int]()
            choices = await organisation_skills(tx, scope, organisation.id, held=kept)
        return PoolForm(organisation, line, tuple(choices))

    async def create(
        self, principal: Principal, slug: str, name: str, skill_ids: Collection[int]
    ) -> CapacityPool:
        """Add a counted pool.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``, ``invalid_configuration``
                (``pool_name``, or ``pool_limit`` when the organisation has as many counted
                pools not archived as the platform allows) or, for ``guaranteed_skill_ids``,
                ``configuration_object_missing``, ``configuration_out_of_scope`` and
                ``invalid_configuration``.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            pool = CapacityPool(
                None, organisation.id, name.strip(), PoolMode.COUNTED, frozenset(skill_ids)
            )
            if await _counted(tx, scope, organisation.id) >= value_of(self._max):
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, LIMIT_FIELD)
            stored = await tx.save_capacity_pool(scope, pool)
            await self._record(tx, principal, slug, f"{CREATED},pool:{stored.id}")
        return stored

    async def update(
        self, principal: Principal, slug: str, pool_id: int, name: str, skill_ids: Collection[int]
    ) -> bool:
        """Change the name and guaranteed skills of a counted pool; return whether it changed.

        Raises:
            DomainError: As ``create`` (without the limit), plus ``configuration_object_missing``
                for a pool that is not the organisation's or is archived and
                ``invalid_configuration`` (``mode``) for a legacy pool.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = await _own_editable_pool(tx, scope, organisation, pool_id)
            changed = CapacityPool(
                current.id,
                current.organisation_id,
                name.strip(),
                current.mode,
                frozenset(skill_ids),
                current.archived_at,
                current.group_id,
            )
            if changed == current:
                return False
            await tx.save_capacity_pool(scope, changed)
            await self._record(tx, principal, slug, f"{CHANGED},pool:{pool_id}")
        return True

    async def archive(self, principal: Principal, slug: str, pool_id: int) -> None:
        """Archive a counted pool.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``configuration_object_missing``, ``invalid_configuration`` (``mode``) for a
                legacy pool, and ``configuration_object_in_use`` (``capacity_pool``) while a flow
                draws on it.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            pool = await _own_editable_pool(tx, scope, organisation, pool_id, archived_ok=True)
            if pool.archived_at is not None:
                return
            now = self._clock.now()
            await tx.archive_capacity_pool(scope, pool_id, now)
            await self._record(tx, principal, slug, f"{ARCHIVED},pool:{pool_id}", now)

    async def _record(
        self,
        tx: Transaction,
        principal: Principal,
        slug: str,
        detail: str,
        at: datetime | None = None,
    ) -> None:
        """Audit a change of the pools in the transaction that made it (numbers only)."""
        await record_change(tx, at or self._clock.now(), principal, _PERMISSION, slug, None, detail)


async def _own_editable_pool(
    tx: Transaction,
    scope: Scope,
    organisation: Organisation,
    pool_id: int,
    *,
    archived_ok: bool = False,
) -> CapacityPool:
    """Return the pool if it is the organisation's own, counted and (unless allowed) not archived.

    Another organisation's pool and an archived one answer like a missing one (A05); a legacy
    pool is never changed here and says so (``mode``), which the screens answer like a missing
    one too.
    """
    pool = own_object(await tx.get_capacity_pool(scope, pool_id), organisation, "pool_id")
    if pool.mode is not PoolMode.COUNTED:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "mode")
    if pool.archived_at is not None and not archived_ok:
        raise missing_object("pool_id")
    return pool


async def _counted(tx: Transaction, scope: Scope, organisation_id: int) -> int:
    """Return how many counted pools of the organisation are not archived (legacy ones are not)."""
    pools = await tx.list_capacity_pools(scope, organisation_id)
    return sum(1 for pool in pools if pool.mode is PoolMode.COUNTED)


async def _skills_by_id(tx: Transaction, scope: Scope, organisation_id: int) -> dict[int, Skill]:
    """Return every skill of the organisation, archived ones too, by id."""
    return {s.id: s for s in await all_skills(tx, scope, organisation_id) if s.id is not None}


async def _line(
    tx: Transaction, scope: Scope, pool: CapacityPool, skills: Mapping[int, Skill]
) -> PoolLine:
    """Return the pool with its skills and the flows that draw on it, as far as ``scope`` sees."""
    drawn: PoolFlows = await tx.flows_on_pool(scope, pool.id or 0)
    flows = []
    for flow_id in drawn.flows:
        flow = await tx.get_service_flow(scope, flow_id)
        if flow is None:
            continue
        group = await tx.get_group_by_id(flow.group_id)
        revision = flow.current or flow.draft
        labels = dict(revision.labels) if revision else {}
        flows.append(FlowRef(labels or {"nl": flow.code}, group.name if group else ""))
    guaranteed = sorted(
        (skills[i] for i in pool.guaranteed_skill_ids if i in skills), key=lambda s: s.code
    )
    return PoolLine(pool, tuple(guaranteed), tuple(flows), drawn.hidden)
