"""Capacity pool use cases: whose time flows draw on and which skills a pool guarantees (ADR 0121).

A *legacy* pool wraps one service's present staffing and is only shown; a *counted* pool is
kept here: a name and the skills every counted unit holds. How many units work per time block
is not set here yet (it arrives with interval accounting). Every operation asks
``AdminService.authorize`` first; every change runs in one locked transaction with its audit
event, carrying the pool's number and never a name.

    overview   capacity.manage   the organisation's pools with the flows that draw on them
    pool       capacity.manage   one pool of the organisation
    create     capacity.manage   a counted pool: name and guaranteed skills
    update     capacity.manage   name and guaranteed skills of a counted pool
    archive    capacity.manage   refused while a flow draws on it
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.capacity import CapacityPool, PoolFlows, PoolMode
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.organisations import organisation_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Organisation
from app.domain.skill_service import organisation_skills
from app.domain.skills import Skill

_PERMISSION = Permission.CAPACITY_MANAGE
CREATED = "capacity_pool.created"
CHANGED = "capacity_pool.changed"
ARCHIVED = "capacity_pool.archived"
"""Details of the audit events (action ``capacity.manage``); the labels are in the audit page."""


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
    """An organisation's pools, the archived ones or those in use."""

    organisation: Organisation
    lines: tuple[PoolLine, ...]
    archived: bool


@dataclass(frozen=True)
class PoolForm:
    """What a pool's form needs: the pool (``None`` for a new one) and the skills to choose from."""

    organisation: Organisation
    line: PoolLine | None
    choices: tuple[Skill, ...]


class CapacityService:
    """Application service for the capacity pools of an organisation.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises changes and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def overview(
        self, principal: Principal, slug: str, *, archived: bool = False
    ) -> PoolOverview:
        """Return the organisation's pools (the archived ones on request).

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            pools = await tx.list_capacity_pools(scope, organisation.id, archived=archived)
            skills = await _skills_by_id(tx, scope, organisation.id)
            lines = [await _line(tx, scope, pool, skills) for pool in pools]
        return PoolOverview(organisation, tuple(lines), archived)

    async def pool(self, principal: Principal, slug: str, pool_id: int) -> PoolLine:
        """Return one pool of the organisation.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``configuration_object_missing`` (also for another organisation's pool).
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            pool = await _own_pool(tx, scope, organisation, pool_id)
            return await _line(tx, scope, pool, await _skills_by_id(tx, scope, organisation.id))

    async def form(self, principal: Principal, slug: str, pool_id: int | None) -> PoolForm:
        """Return what the form of a new pool (``None``) or of ``pool_id`` shows.

        The choices are the skills not archived, and an archived one the pool still guarantees.

        Raises:
            DomainError: As ``pool``.
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            skills = await _skills_by_id(tx, scope, organisation.id)
            line = None
            if pool_id is not None:
                pool = await _own_pool(tx, scope, organisation, pool_id)
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
                (``pool_name``) or, for ``guaranteed_skill_ids``, ``configuration_object_missing``,
                ``configuration_out_of_scope`` and ``invalid_configuration``.
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            pool = CapacityPool(
                None, organisation.id, name.strip(), PoolMode.COUNTED, frozenset(skill_ids)
            )
            stored = await tx.save_capacity_pool(scope, pool)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                _PERMISSION,
                slug,
                None,
                f"{CREATED},pool:{stored.id}",
            )
        return stored

    async def update(
        self, principal: Principal, slug: str, pool_id: int, name: str, skill_ids: Collection[int]
    ) -> bool:
        """Change the name and guaranteed skills of a counted pool; return whether it changed.

        Raises:
            DomainError: As ``create``, plus ``configuration_object_missing`` for a pool that is
                not the organisation's and ``invalid_configuration`` (``mode``) for a legacy pool.
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = await _own_counted_pool(tx, scope, organisation, pool_id)
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
            await record_change(
                tx,
                self._clock.now(),
                principal,
                _PERMISSION,
                slug,
                None,
                f"{CHANGED},pool:{pool_id}",
            )
        return True

    async def archive(self, principal: Principal, slug: str, pool_id: int) -> None:
        """Archive a counted pool.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``configuration_object_missing``, ``invalid_configuration`` (``mode``) for a
                legacy pool, and ``configuration_object_in_use`` (``capacity_pool``) while a flow
                draws on it.
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            pool = await _own_counted_pool(tx, scope, organisation, pool_id)
            if pool.archived_at is not None:
                return
            now = self._clock.now()
            await tx.archive_capacity_pool(scope, pool_id, now)
            await record_change(
                tx, now, principal, _PERMISSION, slug, None, f"{ARCHIVED},pool:{pool_id}"
            )

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``capacity.manage`` on ``slug``."""
        require(await self._admin.authorize(principal, _PERMISSION, organisation_slug=slug))


async def _own_pool(
    tx: Transaction, scope: Scope, organisation: Organisation, pool_id: int
) -> CapacityPool:
    """Return the pool if it exists inside ``scope`` and is the organisation's own (A05)."""
    pool = await tx.get_capacity_pool(scope, pool_id)
    if pool is None or pool.organisation_id != organisation.id:
        raise missing_object("pool_id")
    return pool


async def _own_counted_pool(
    tx: Transaction, scope: Scope, organisation: Organisation, pool_id: int
) -> CapacityPool:
    """Return the pool as ``_own_pool``, refusing a legacy pool: it is never changed here."""
    pool = await _own_pool(tx, scope, organisation, pool_id)
    if pool.mode is not PoolMode.COUNTED:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "mode")
    return pool


async def _skills_by_id(tx: Transaction, scope: Scope, organisation_id: int) -> dict[int, Skill]:
    """Return every skill of the organisation, archived ones too, by id."""
    every = await tx.list_skills(scope, organisation_id)
    every += await tx.list_skills(scope, organisation_id, archived=True)
    return {skill.id: skill for skill in every if skill.id is not None}


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
