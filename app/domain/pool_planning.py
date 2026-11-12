"""Use cases for the staffing of a counted capacity pool (ADR 0122, step 4).

A counted pool has its own weekly blocks, per-date exceptions and time zone, shaped like a
service's staffing (``app.domain.planning``) with ``capacity`` meaning *units*: people counted.
Only ``capacity.manage`` changes them (decision P2), so every operation here, reading included,
asks the matrix first (``AdminService.scoped``) and every change runs in one locked transaction
that validates the whole schedule (``check_pool_staffing``), writes and audits with the pool's
code and what changed, never a value or a name. A pool of another organisation, an archived one
and a legacy pool answer like a missing one.

    staffing      the pool's time zone, blocks, coming exceptions and coming shortfalls
    rule, override   one block or exception, to change it
    set_timezone  the zone the blocks are read in; blocks can be entered only after it
    add/update/split/remove rule, add/update/remove override

Nothing here changes a booking: existing appointments stay (decision P3) and a drop in staffing
shows as a shortfall, a stretch where the commitments of the pool exceed its staffed units. The
pool's staffing is not read by any booking path yet (``ROLLBACK_SAFE`` is false until step 5).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.domain.admin import AdminService
from app.domain.authorization import Permission, Principal, Scope
from app.domain.capacity import CapacityPool
from app.domain.capacity_service import own_editable_pool, pool_ref
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.holidays import closures
from app.domain.organisations import organisation_by_slug
from app.domain.pool_accounting import Shortfall, shortfalls
from app.domain.pool_staffing import (
    PoolStaffing,
    check_pool_staffing,
    check_timezone,
    supply_between,
)
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import (
    Organisation,
    ScheduleOverride,
    ScheduleRule,
    split_block,
)
from app.domain.tunable import value_for

_PERMISSION = Permission.CAPACITY_MANAGE

TIMEZONE_CHANGED = "pool_staffing.timezone"
RULE_ADDED = "pool_staffing.rule_add"
RULE_CHANGED = "pool_staffing.rule_edit"
RULE_SPLIT = "pool_staffing.rule_split"
RULE_REMOVED = "pool_staffing.rule_remove"
OVERRIDE_ADDED = "pool_staffing.override_add"
OVERRIDE_CHANGED = "pool_staffing.override_edit"
OVERRIDE_REMOVED = "pool_staffing.override_remove"
"""Details of the audit events (action ``capacity.manage``); the labels are in the audit page."""

RULE_FIELD = "rule_id"
OVERRIDE_FIELD = "override_id"
"""The field of the refusal for a block or exception the pool does not have."""


@dataclass(frozen=True)
class PoolStaffingPage:
    """What the staffing pages of one counted pool show.

    Attributes:
        organisation: The pool's organisation.
        pool: The pool (not archived, counted).
        timezone: Name of the pool's time zone, ``None`` until one is chosen; the blocks and
            exceptions below are empty until then.
        rules: The weekly blocks, in week order.
        overrides: The exceptions from today (pool time) on, by date.
        shortfalls: The stretches within ``horizon_days`` where more units are committed than
            staffed, earliest first.
        horizon_days: How far ahead the shortfalls were looked for (``shortfall_horizon_days``).
    """

    organisation: Organisation
    pool: CapacityPool
    timezone: str | None
    rules: tuple[ScheduleRule, ...]
    overrides: tuple[ScheduleOverride, ...]
    shortfalls: tuple[Shortfall, ...]
    horizon_days: int


class PoolPlanningService:
    """Application service for the staffing of the counted pools of an organisation.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises every operation and audits refusals and changes.
        horizon_days: How far ahead shortfalls are looked for: the class D setting
            ``shortfall_horizon_days`` (a function of the organisation id) or a plain number.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        horizon_days: int | Callable[[int], int],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._horizon_days = horizon_days

    async def staffing(self, principal: Principal, slug: str, pool_id: int) -> PoolStaffingPage:
        """Return the staffing of a counted pool with the shortfalls ahead.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``configuration_object_missing`` (another organisation's pool, an archived one)
                or ``invalid_configuration`` (``mode``) for a legacy pool.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        now = self._clock.now()
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            pool = await own_editable_pool(tx, scope, organisation, pool_id)
            found = await tx.get_pool_staffing(scope, pool_id)
            horizon = value_for(self._horizon_days, organisation.id)
            if found is None:
                return PoolStaffingPage(organisation, pool, None, (), (), (), horizon)
            today = now.astimezone(found.timezone).date()
            upcoming = tuple(o for o in found.overrides if o.day >= today)
            short = await _shortfalls(tx, organisation, pool_id, found, now, horizon)
        return PoolStaffingPage(
            organisation,
            pool,
            found.timezone.key,
            found.rules,
            upcoming,
            short,
            horizon,
        )

    async def rule(
        self, principal: Principal, slug: str, pool_id: int, rule_id: int
    ) -> ScheduleRule:
        """Return the pool's weekly block ``rule_id``, to change it.

        Raises:
            DomainError: As ``staffing``, and ``configuration_object_missing`` (``rule_id``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.read() as tx:
            await _own_pool(tx, scope, slug, pool_id)
            staffing = await tx.get_pool_staffing(scope, pool_id)
        return _rule_by_id(staffing.rules if staffing else (), rule_id)

    async def override(
        self, principal: Principal, slug: str, pool_id: int, override_id: int
    ) -> ScheduleOverride:
        """Return the pool's exception ``override_id``, to change it.

        Raises:
            DomainError: As ``staffing``, and ``configuration_object_missing`` (``override_id``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.read() as tx:
            await _own_pool(tx, scope, slug, pool_id)
            staffing = await tx.get_pool_staffing(scope, pool_id)
        return _override_by_id(staffing.overrides if staffing else (), override_id)

    async def set_timezone(
        self, principal: Principal, slug: str, pool_id: int, timezone: str
    ) -> bool:
        """Set the time zone the pool's blocks are read in; return whether it changed.

        Raises:
            DomainError: As ``staffing``, and ``invalid_configuration`` (``timezone``) for a name
                that is no time zone of the tz database.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            check_timezone(timezone)
            before = await tx.get_pool_staffing(scope, pool_id)
            if before is not None and before.timezone.key == timezone:
                return False
            await tx.set_pool_timezone(scope, pool_id, timezone)
            await self._record(tx, principal, slug, TIMEZONE_CHANGED, pool)
        return True

    async def add_rule(
        self, principal: Principal, slug: str, pool_id: int, rule: ScheduleRule
    ) -> int:
        """Add a weekly block; it may not overlap another one.

        Raises:
            DomainError: As ``staffing``, and ``invalid_configuration`` (``timezone`` before one
                is chosen, ``schedule_rules`` for an overlap, ``subjects``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing = await _staffing(tx, scope, pool_id)
            check_pool_staffing(_with(staffing, rules=(*staffing.rules, rule)))
            rule_id = await tx.add_pool_rule(scope, pool_id, rule)
            if rule_id is None:  # pragma: no cover - the pool was just read in this transaction
                raise missing_object("pool_id")
            await self._record(tx, principal, slug, RULE_ADDED, pool)
        return rule_id

    async def update_rule(
        self, principal: Principal, slug: str, pool_id: int, rule_id: int, rule: ScheduleRule
    ) -> bool:
        """Replace weekly block ``rule_id``; ``False`` if nothing changed.

        Raises:
            DomainError: As ``add_rule``, and ``configuration_object_missing`` (``rule_id``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing = await _staffing(tx, scope, pool_id)
            if _rule_by_id(staffing.rules, rule_id) == rule:
                return False
            others = tuple(r for r in staffing.rules if r.id != rule_id)
            check_pool_staffing(_with(staffing, rules=(*others, rule)))
            await tx.update_pool_rule(scope, pool_id, rule_id, rule)
            await self._record(tx, principal, slug, RULE_CHANGED, pool)
        return True

    async def split_rule(
        self,
        principal: Principal,
        slug: str,
        pool_id: int,
        rule_id: int,
        at: time,
        capacity: int,
        label: str | None = None,
    ) -> int:
        """Split weekly block ``rule_id`` at ``at`` into two touching blocks (ADR 0027).

        Returns the id of the second part.

        Raises:
            DomainError: As ``update_rule``, and ``invalid_configuration`` (``split_at``,
                ``capacity``, ``label``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing = await _staffing(tx, scope, pool_id)
            first, second = split_block(_rule_by_id(staffing.rules, rule_id), at, capacity, label)
            others = tuple(r for r in staffing.rules if r.id != rule_id)
            check_pool_staffing(_with(staffing, rules=(*others, first, second)))
            await tx.update_pool_rule(scope, pool_id, rule_id, first)
            second_id = await tx.add_pool_rule(scope, pool_id, second)
            if second_id is None:  # pragma: no cover - the pool was just read in this transaction
                raise missing_object("pool_id")
            await self._record(tx, principal, slug, RULE_SPLIT, pool)
        return second_id

    async def remove_rule(
        self, principal: Principal, slug: str, pool_id: int, rule_id: int
    ) -> None:
        """Remove a weekly block of the pool.

        Raises:
            DomainError: As ``staffing``, and ``configuration_object_missing`` (``rule_id``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            if not await tx.delete_pool_rule(scope, pool_id, rule_id):
                raise missing_object(RULE_FIELD)
            await self._record(tx, principal, slug, RULE_REMOVED, pool)

    async def add_override(
        self, principal: Principal, slug: str, pool_id: int, override: ScheduleOverride
    ) -> int:
        """Close a date or give it its own blocks; the date may not lie in the past (pool time).

        Raises:
            DomainError: As ``add_rule``, and ``invalid_configuration`` (``day``,
                ``schedule_overrides``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing = await _staffing(tx, scope, pool_id)
            self._check_day(override, staffing)
            check_pool_staffing(_with(staffing, overrides=(*staffing.overrides, override)))
            override_id = await tx.add_pool_override(scope, pool_id, override)
            if override_id is None:  # pragma: no cover - the pool was just read in this transaction
                raise missing_object("pool_id")
            await self._record(tx, principal, slug, OVERRIDE_ADDED, pool)
        return override_id

    async def update_override(
        self,
        principal: Principal,
        slug: str,
        pool_id: int,
        override_id: int,
        override: ScheduleOverride,
    ) -> bool:
        """Replace exception ``override_id``; ``False`` if nothing changed.

        Raises:
            DomainError: As ``add_override``, and ``configuration_object_missing``
                (``override_id``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing = await _staffing(tx, scope, pool_id)
            if _override_by_id(staffing.overrides, override_id) == override:
                return False
            self._check_day(override, staffing)
            others = tuple(o for o in staffing.overrides if o.id != override_id)
            check_pool_staffing(_with(staffing, overrides=(*others, override)))
            await tx.update_pool_override(scope, pool_id, override_id, override)
            await self._record(tx, principal, slug, OVERRIDE_CHANGED, pool)
        return True

    async def remove_override(
        self, principal: Principal, slug: str, pool_id: int, override_id: int
    ) -> None:
        """Remove an exception of the pool.

        Raises:
            DomainError: As ``staffing``, and ``configuration_object_missing`` (``override_id``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            if not await tx.delete_pool_override(scope, pool_id, override_id):
                raise missing_object(OVERRIDE_FIELD)
            await self._record(tx, principal, slug, OVERRIDE_REMOVED, pool)

    def _check_day(self, override: ScheduleOverride, staffing: PoolStaffing) -> None:
        """Refuse an exception dated before today in the pool's time zone (``day``)."""
        today = self._clock.now().astimezone(staffing.timezone).date()
        if override.day < today:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "day")

    async def _record(
        self, tx: Transaction, principal: Principal, slug: str, what: str, pool: CapacityPool
    ) -> None:
        """Audit a change of the pool's staffing in the transaction that made it."""
        await self._admin.record_change(
            tx,
            self._clock.now(),
            principal,
            _PERMISSION,
            slug,
            None,
            f"{what},pool:{pool_ref(pool)}",
        )


async def _own_pool(
    tx: Transaction, scope: Scope, slug: str, pool_id: int
) -> tuple[Organisation, CapacityPool]:
    """Return the organisation and its counted pool ``pool_id`` (not archived, not another's)."""
    organisation = await organisation_by_slug(tx, slug)
    return organisation, await own_editable_pool(tx, scope, organisation, pool_id)


async def _staffing(tx: Transaction, scope: Scope, pool_id: int) -> PoolStaffing:
    """Return the pool's blocks and all its exceptions.

    Raises:
        DomainError: ``invalid_configuration`` (``timezone``) while the pool has no time zone:
            its blocks cannot be read before one is chosen.
    """
    staffing = await tx.get_pool_staffing(scope, pool_id)
    if staffing is None:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "timezone")
    return staffing


def _with(
    staffing: PoolStaffing,
    *,
    rules: tuple[ScheduleRule, ...] | None = None,
    overrides: tuple[ScheduleOverride, ...] | None = None,
) -> PoolStaffing:
    """Return ``staffing`` with its blocks or exceptions replaced."""
    return PoolStaffing(
        staffing.timezone,
        staffing.rules if rules is None else rules,
        staffing.overrides if overrides is None else overrides,
    )


def _rule_by_id(rules: tuple[ScheduleRule, ...], rule_id: int) -> ScheduleRule:
    """Return the pool's block with ``rule_id`` (another pool's answers like none)."""
    for rule in rules:
        if rule.id == rule_id:
            return rule
    raise missing_object(RULE_FIELD)


def _override_by_id(overrides: tuple[ScheduleOverride, ...], override_id: int) -> ScheduleOverride:
    """Return the pool's exception with ``override_id`` (another pool's answers like none)."""
    for override in overrides:
        if override.id == override_id:
            return override
    raise missing_object(OVERRIDE_FIELD)


async def _shortfalls(
    tx: Transaction,
    organisation: Organisation,
    pool_id: int,
    staffing: PoolStaffing,
    now: datetime,
    horizon_days: int,
) -> tuple[Shortfall, ...]:
    """Return the stretches in the next ``horizon_days`` with more commitments than units.

    The supply is the pool's staffing with the organisation's holidays closed (an own exception
    wins); the load is every place held in the pool, derived from the bookings. A booking is
    never touched here (decision P3).
    """
    upper = now + timedelta(days=horizon_days)
    first = now.astimezone(staffing.timezone).date()
    last = upper.astimezone(staffing.timezone).date()
    supply = supply_between(staffing, await _closed_days(tx, organisation, first, last), now, upper)
    commitments = await tx.get_pool_commitments(pool_id, now, upper)
    return tuple(shortfalls(supply, commitments, now, upper))


async def _closed_days(
    tx: Transaction, organisation: Organisation, first: date, last: date
) -> list[date]:
    """Return the dates the organisation's holiday calendars close, between the two dates."""
    calendar_ids = await tx.get_organisation_holiday_calendars(organisation.id)
    if not calendar_ids:
        return []
    return [h.day for h in closures(await tx.get_holidays(calendar_ids, first, last), ())]
