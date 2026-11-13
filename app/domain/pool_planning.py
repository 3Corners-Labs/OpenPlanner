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
    set_max_waiting  how many people without an appointment may wait (ADR 0121 step 7)
    add/update/split/remove rule, add/update/remove override

Nothing here changes a booking: existing appointments stay (decision P3) and a drop in staffing
shows as a shortfall, a stretch where the commitments of the pool exceed its staffed units. The
pool's staffing is not read by any booking path yet (``ROLLBACK_SAFE`` is false until step 5).
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.domain.admin import AdminService
from app.domain.authorization import Permission, Principal, Scope
from app.domain.capacity import CapacityPool, check_max_waiting
from app.domain.capacity_service import own_editable_pool, pool_ref
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.holidays import closures
from app.domain.organisations import organisation_by_slug
from app.domain.pool_accounting import Shortfall, shortfall_page
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
MAX_WAITING_CHANGED = "pool_staffing.max_waiting"
RULE_ADDED = "pool_staffing.rule_add"
RULE_CHANGED = "pool_staffing.rule_edit"
RULE_SPLIT = "pool_staffing.rule_split"
RULE_REMOVED = "pool_staffing.rule_remove"
OVERRIDE_ADDED = "pool_staffing.override_add"
OVERRIDE_CHANGED = "pool_staffing.override_edit"
OVERRIDE_REMOVED = "pool_staffing.override_remove"
"""Details of the audit events (action ``capacity.manage``); the labels are in the audit page."""

MAX_SHORTFALLS_LISTED = 20
"""How many shortfalls the page model carries; the rest is only counted (a display cap)."""

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
        shortfalls: The first ``MAX_SHORTFALLS_LISTED`` stretches within ``horizon_days``
            where more units are committed than staffed, earliest first.
        shortfall_total: How many such stretches there are in all.
        horizon_days: How far ahead the shortfalls were looked for (``shortfall_horizon_days``).
        max_waiting: How many people without an appointment may wait for the pool at once;
            ``None`` is no limit (ADR 0121 step 7).
    """

    organisation: Organisation
    pool: CapacityPool
    timezone: str | None
    rules: tuple[ScheduleRule, ...]
    overrides: tuple[ScheduleOverride, ...]
    shortfalls: tuple[Shortfall, ...]
    shortfall_total: int
    horizon_days: int
    max_waiting: int | None = None


@dataclass(frozen=True)
class PoolRule:
    """One weekly block with the pool and organisation it belongs to, and its version."""

    organisation: Organisation
    pool: CapacityPool
    rule: ScheduleRule
    version: str


@dataclass(frozen=True)
class PoolOverride:
    """One exception with the pool and organisation it belongs to, and its version."""

    organisation: Organisation
    pool: CapacityPool
    override: ScheduleOverride
    version: str


def _digest(*parts: object) -> str:
    """Return a short hash of ``parts``: what a form carries to tell the row it was loaded from."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def rule_version(rule: ScheduleRule) -> str:
    """Return the version of a block: a hash of its stored values.

    A change form carries it back as ``expected``; a save or removal whose version differs from
    the stored row's is refused (``pool_staffing_changed``), so an admin never overwrites or
    deletes a block that someone else changed after the form was opened.
    """
    w = rule.window
    return _digest(
        rule.id,
        rule.weekday,
        w.opens,
        w.closes,
        w.capacity,
        w.label,
        w.offer.value,
        rule.valid_from,
        rule.valid_to,
    )


def override_version(override: ScheduleOverride) -> str:
    """Return the version of an exception: a hash of its stored values (see ``rule_version``)."""
    w = override.window
    return _digest(
        override.id,
        override.day,
        *(() if w is None else (w.opens, w.closes, w.capacity, w.label, w.offer.value)),
    )


def _unchanged(stored_version: str, expected: str) -> None:
    """Require that the row still has the version the form was loaded with.

    Raises:
        DomainError: ``pool_staffing_changed`` when it does not (or no version was sent).
    """
    if stored_version != expected:
        raise DomainError(ErrorCode.POOL_STAFFING_CHANGED)


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
            waiting = await tx.get_pool_max_waiting(pool_id)
            if found is None:
                return PoolStaffingPage(organisation, pool, None, (), (), (), 0, horizon, waiting)
            today = now.astimezone(found.timezone).date()
            upcoming = tuple(o for o in found.overrides if o.day >= today)
            short, total = await _shortfalls(tx, organisation, pool_id, found, now, horizon)
        return PoolStaffingPage(
            organisation,
            pool,
            found.timezone.key,
            found.rules,
            upcoming,
            tuple(short),
            total,
            horizon,
            waiting,
        )

    async def rule(self, principal: Principal, slug: str, pool_id: int, rule_id: int) -> PoolRule:
        """Return the pool's weekly block ``rule_id``, to change it.

        Raises:
            DomainError: As ``staffing``, and ``configuration_object_missing`` (``rule_id``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.read() as tx:
            organisation, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing = await tx.get_pool_staffing(scope, pool_id)
        rule = _rule_by_id(staffing.rules if staffing else (), rule_id)
        return PoolRule(organisation, pool, rule, rule_version(rule))

    async def override(
        self, principal: Principal, slug: str, pool_id: int, override_id: int
    ) -> PoolOverride:
        """Return the pool's exception ``override_id``, to change it.

        Raises:
            DomainError: As ``staffing``, and ``configuration_object_missing`` (``override_id``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.read() as tx:
            organisation, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing = await tx.get_pool_staffing(scope, pool_id)
        override = _override_by_id(staffing.overrides if staffing else (), override_id)
        return PoolOverride(organisation, pool, override, override_version(override))

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

    async def set_max_waiting(
        self, principal: Principal, slug: str, pool_id: int, limit: int | None
    ) -> bool:
        """Set how many people without an appointment may wait for the pool; whether it changed.

        Configuration of one pool (ADR 0121 step 7, ADR 0122 *Settings*); ``None`` is no limit.

        Raises:
            DomainError: As ``staffing``, and ``invalid_configuration`` (``max_waiting``)
                outside 1 to 1000.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        check_max_waiting(limit)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            if await tx.get_pool_max_waiting(pool_id) == limit:
                return False
            await tx.set_pool_max_waiting(scope, pool_id, limit)
            await self._record(tx, principal, slug, MAX_WAITING_CHANGED, pool)
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
            staffing, _ = await _staffing(tx, scope, pool_id, self._clock.now())
            check_pool_staffing(_with(staffing, rules=(*staffing.rules, rule)))
            rule_id = await tx.add_pool_rule(scope, pool_id, rule)
            if rule_id is None:  # pragma: no cover - the pool was just read in this transaction
                raise missing_object("pool_id")
            await self._record(tx, principal, slug, RULE_ADDED, pool)
        return rule_id

    async def update_rule(
        self,
        principal: Principal,
        slug: str,
        pool_id: int,
        rule_id: int,
        rule: ScheduleRule,
        expected: str,
    ) -> bool:
        """Replace weekly block ``rule_id`` that the form loaded as ``expected``.

        Returns ``False`` if nothing changed.

        Raises:
            DomainError: As ``add_rule``, ``configuration_object_missing`` (``rule_id``) and
                ``pool_staffing_changed`` when the block is no longer the version loaded.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing, _ = await _staffing(tx, scope, pool_id, self._clock.now())
            stored = _rule_by_id(staffing.rules, rule_id)
            _unchanged(rule_version(stored), expected)
            if stored == rule:
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
        label: str | None,
        expected: str,
    ) -> int:
        """Split weekly block ``rule_id`` at ``at`` into two touching blocks (ADR 0027).

        Returns the id of the second part.

        Raises:
            DomainError: As ``update_rule`` (``expected`` is the version the form loaded), and
                ``invalid_configuration`` (``split_at``, ``capacity``, ``label``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing, _ = await _staffing(tx, scope, pool_id, self._clock.now())
            stored = _rule_by_id(staffing.rules, rule_id)
            _unchanged(rule_version(stored), expected)
            first, second = split_block(stored, at, capacity, label)
            others = tuple(r for r in staffing.rules if r.id != rule_id)
            check_pool_staffing(_with(staffing, rules=(*others, first, second)))
            await tx.update_pool_rule(scope, pool_id, rule_id, first)
            second_id = await tx.add_pool_rule(scope, pool_id, second)
            if second_id is None:  # pragma: no cover - the pool was just read in this transaction
                raise missing_object("pool_id")
            await self._record(tx, principal, slug, RULE_SPLIT, pool)
        return second_id

    async def remove_rule(
        self, principal: Principal, slug: str, pool_id: int, rule_id: int, expected: str
    ) -> None:
        """Remove weekly block ``rule_id`` that the form loaded as ``expected``.

        Raises:
            DomainError: As ``staffing``, ``configuration_object_missing`` (``rule_id``) and
                ``pool_staffing_changed`` when the block is no longer the version loaded.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            found = await tx.get_pool_staffing(scope, pool_id)
            stored = _rule_by_id(found.rules if found else (), rule_id)
            _unchanged(rule_version(stored), expected)
            if not await tx.delete_pool_rule(scope, pool_id, rule_id):  # pragma: no cover
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
            staffing, past = await _staffing(tx, scope, pool_id, self._clock.now())
            self._check_day(override, staffing)
            check_pool_staffing(_with(staffing, overrides=(*staffing.overrides, override)))
            await _drop(tx, scope, pool_id, past)
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
        expected: str,
    ) -> bool:
        """Replace exception ``override_id`` that the form loaded as ``expected``.

        Returns ``False`` if nothing changed.

        Raises:
            DomainError: As ``add_override``, ``configuration_object_missing``
                (``override_id``) and ``pool_staffing_changed`` when the exception is no longer
                the version loaded.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            staffing, past = await _staffing(tx, scope, pool_id, self._clock.now())
            stored = _override_by_id(staffing.overrides, override_id)
            _unchanged(override_version(stored), expected)
            if stored == override:
                return False
            self._check_day(override, staffing)
            others = tuple(o for o in staffing.overrides if o.id != override_id)
            check_pool_staffing(_with(staffing, overrides=(*others, override)))
            await _drop(tx, scope, pool_id, past)
            await tx.update_pool_override(scope, pool_id, override_id, override)
            await self._record(tx, principal, slug, OVERRIDE_CHANGED, pool)
        return True

    async def remove_override(
        self, principal: Principal, slug: str, pool_id: int, override_id: int, expected: str
    ) -> None:
        """Remove exception ``override_id`` that the form loaded as ``expected``.

        Raises:
            DomainError: As ``staffing``, ``configuration_object_missing`` (``override_id``) and
                ``pool_staffing_changed`` when the exception is no longer the version loaded.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            _, pool = await _own_pool(tx, scope, slug, pool_id)
            found = await tx.get_pool_staffing(scope, pool_id)
            stored = _override_by_id(found.overrides if found else (), override_id)
            _unchanged(override_version(stored), expected)
            if not await tx.delete_pool_override(scope, pool_id, override_id):  # pragma: no cover
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


async def _staffing(
    tx: Transaction, scope: Scope, pool_id: int, now: datetime
) -> tuple[PoolStaffing, tuple[int, ...]]:
    """Return the pool's blocks and its exceptions from today (pool time) on, and the past ones.

    A past exception can overlap nothing that matters and is neither listed nor changed, so it
    is neither validated nor counted against ``MAX_POOL_OVERRIDES``; the ids of the past ones are
    returned for the change that drops them (``_drop``).

    Raises:
        DomainError: ``invalid_configuration`` (``timezone``) while the pool has no time zone:
            its blocks cannot be read before one is chosen.
    """
    found = await tx.get_pool_staffing(scope, pool_id)
    if found is None:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "timezone")
    today = now.astimezone(found.timezone).date()
    coming = tuple(o for o in found.overrides if o.day >= today)
    past = tuple(o.id for o in found.overrides if o.day < today and o.id is not None)
    return PoolStaffing(found.timezone, found.rules, coming), past


async def _drop(tx: Transaction, scope: Scope, pool_id: int, override_ids: tuple[int, ...]) -> None:
    """Delete the past exceptions ``override_ids``, in the transaction of the change at hand."""
    for override_id in override_ids:
        await tx.delete_pool_override(scope, pool_id, override_id)


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
) -> tuple[list[Shortfall], int]:
    """Return the first stretches in the next ``horizon_days`` with more commitments than units.

    Only ``MAX_SHORTFALLS_LISTED`` are built; the second value counts all of them.

    The supply is the pool's staffing with the organisation's holidays closed (an own exception
    wins); the load is every place held in the pool, derived from the bookings. A booking is
    never touched here (decision P3).
    """
    upper = now + timedelta(days=horizon_days)
    first = now.astimezone(staffing.timezone).date()
    last = upper.astimezone(staffing.timezone).date()
    supply = supply_between(staffing, await _closed_days(tx, organisation, first, last), now, upper)
    commitments = await tx.get_pool_commitments(pool_id, now, upper)
    return shortfall_page(supply, commitments, now, upper, MAX_SHORTFALLS_LISTED)


async def _closed_days(
    tx: Transaction, organisation: Organisation, first: date, last: date
) -> list[date]:
    """Return the dates the organisation's holiday calendars close, between the two dates."""
    calendar_ids = await tx.get_organisation_holiday_calendars(organisation.id)
    if not calendar_ids:
        return []
    return [h.day for h in closures(await tx.get_holidays(calendar_ids, first, last), ())]
