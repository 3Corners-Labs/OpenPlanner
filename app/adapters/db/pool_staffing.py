"""SQL for the staffing of a counted capacity pool and for the slots that draw on it (ADR 0122).

A mixin of ``SqlTransaction``. Staffing (weekly blocks, exceptions, the pool's time zone) is
configuration of the pool: every method takes the ``Scope``, and an id outside it, a legacy pool
or a missing one behaves like a missing one (``False`` or ``None``). What a block may hold
(overlaps, subjects, the bounds) is ``app.domain.pool_staffing.check_pool_staffing``, which the
use case of a later step calls before it writes; the database keeps the same checks that a
service's blocks have. The write methods rely on the immediate write lock of ``locked()`` and
must not be called from a ``read()`` transaction.

The slot methods carry no scope: like ``get_slot_usage`` they serve the booking path, which has
already decided what the caller may see. Nothing reads them for a booking decision yet (the
rollback gate ``ROLLBACK_SAFE`` is false until ADR 0122 step 5).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.adapters.db.capacity_pools import CapacityPoolQueries
from app.adapters.db.models import (
    BookingRow,
    CapacityPoolRow,
    PoolStaffingOverrideRow,
    PoolStaffingRuleRow,
    RescheduleProposalRow,
    SlotRow,
)
from app.adapters.db.proposals import open_proposal
from app.adapters.db.service_requests import open_occupations
from app.domain.authorization import Scope
from app.domain.booking import HOLDS_PLACE, BookingKind, Slot
from app.domain.capacity import PoolMode, check_max_waiting
from app.domain.errors import DomainError, ErrorCode
from app.domain.pool_accounting import MAX_SPAN, Commitment
from app.domain.pool_staffing import PoolStaffing, check_timezone, zone_or_none
from app.domain.scheduling import Offer, ScheduleOverride, ScheduleRule, TimeWindow

_HOLDS_PLACE = sorted(status.value for status in HOLDS_PLACE)


def _rule(row: PoolStaffingRuleRow) -> ScheduleRule:
    """Map a block row; ``units`` is the window's ``capacity``."""
    window = TimeWindow(row.opens, row.closes, row.units, row.label, Offer(row.offer))
    return ScheduleRule(row.weekday, window, row.valid_from, row.valid_to, id=row.id)


def _override(row: PoolStaffingOverrideRow) -> ScheduleOverride:
    """Map an exception row; NULL window columns mean a closed day."""
    if row.opens is None or row.closes is None or row.units is None:
        return ScheduleOverride(day=row.day, window=None, id=row.id)
    window = TimeWindow(row.opens, row.closes, row.units, row.label, Offer(row.offer))
    return ScheduleOverride(day=row.day, window=window, id=row.id)


def _no_subjects(window: TimeWindow | None) -> None:
    """Refuse a window with subjects: a pool stores none, so they would be dropped silently."""
    if window is not None and window.subjects:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "subjects")


def _rule_columns(rule: ScheduleRule) -> dict[str, Any]:
    """Return the stored columns of a block (without its id and pool)."""
    _no_subjects(rule.window)
    return {
        "weekday": rule.weekday,
        "opens": rule.window.opens,
        "closes": rule.window.closes,
        "units": rule.window.capacity,
        "label": rule.window.label,
        "offer": rule.window.offer.value,
        "valid_from": rule.valid_from,
        "valid_to": rule.valid_to,
    }


def _override_columns(override: ScheduleOverride) -> dict[str, Any]:
    """Return the stored columns of an exception; no window means a closed day."""
    window = override.window
    _no_subjects(window)
    return {
        "day": override.day,
        "opens": window.opens if window else None,
        "closes": window.closes if window else None,
        "units": window.capacity if window else None,
        "label": window.label if window else None,
        "offer": window.offer.value if window else Offer.SCHEDULED.value,
    }


class PoolStaffingQueries(CapacityPoolQueries):
    """The pool staffing and pool slots' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def _counted_pool_row(self, scope: Scope, pool_id: int) -> CapacityPoolRow | None:
        """Return the pool row when it is a counted pool inside ``scope``, else ``None``."""
        row = await self._pool_row(scope, pool_id)
        return row if row is not None and row.mode == PoolMode.COUNTED.value else None

    async def get_pool_staffing(
        self, scope: Scope, pool_id: int, from_day: date | None = None
    ) -> PoolStaffing | None:
        """Return a counted pool's blocks and exceptions (from ``from_day`` on), each with its id.

        ``None`` for a missing pool, one outside ``scope``, a legacy pool, and a counted pool
        that has no time zone yet (nothing can be staffed before the zone is chosen).
        """
        row = await self._counted_pool_row(scope, pool_id)
        zone = zone_or_none(row.timezone) if row is not None else None
        if row is None or zone is None:
            return None
        rules = await self._session.scalars(
            select(PoolStaffingRuleRow)
            .where(PoolStaffingRuleRow.pool_id == row.id)
            .order_by(PoolStaffingRuleRow.weekday, PoolStaffingRuleRow.opens)
        )
        query = select(PoolStaffingOverrideRow).where(PoolStaffingOverrideRow.pool_id == row.id)
        if from_day is not None:
            query = query.where(PoolStaffingOverrideRow.day >= from_day)
        overrides = await self._session.scalars(
            query.order_by(PoolStaffingOverrideRow.day, PoolStaffingOverrideRow.opens)
        )
        return PoolStaffing(
            zone,
            tuple(_rule(r) for r in rules),
            tuple(_override(o) for o in overrides),
        )

    async def set_pool_timezone(self, scope: Scope, pool_id: int, timezone: str | None) -> bool:
        """Set the time zone of a counted pool; ``False`` for a pool that is not one in ``scope``.

        ``None`` clears it.

        Raises:
            DomainError: ``invalid_configuration`` with field ``timezone`` for a name that is
                not an IANA time zone known to this host.
        """
        if timezone is not None:
            check_timezone(timezone)
        if await self._counted_pool_row(scope, pool_id) is None:
            return False
        await self._session.execute(
            update(CapacityPoolRow).where(CapacityPoolRow.id == pool_id).values(timezone=timezone)
        )
        return True

    async def get_pool_max_waiting(self, pool_id: int) -> int | None:
        """Return the pool's waiting limit (ADR 0121 step 7); ``None`` is no limit."""
        found: int | None = await self._session.scalar(
            select(CapacityPoolRow.max_waiting).where(CapacityPoolRow.id == pool_id)
        )
        return found

    async def set_pool_max_waiting(self, scope: Scope, pool_id: int, limit: int | None) -> bool:
        """Set the waiting limit of a counted pool; ``False`` for a pool that is not one in scope.

        Raises:
            DomainError: ``invalid_configuration`` (field ``max_waiting``) outside 1 to 1000.
        """
        check_max_waiting(limit)
        if await self._counted_pool_row(scope, pool_id) is None:
            return False
        await self._session.execute(
            update(CapacityPoolRow).where(CapacityPoolRow.id == pool_id).values(max_waiting=limit)
        )
        return True

    async def add_pool_rule(self, scope: Scope, pool_id: int, rule: ScheduleRule) -> int | None:
        """Insert a block of a counted pool and return its id; ``None`` when no such pool."""
        if await self._counted_pool_row(scope, pool_id) is None:
            return None
        row = PoolStaffingRuleRow(pool_id=pool_id, **_rule_columns(rule))
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def update_pool_rule(
        self, scope: Scope, pool_id: int, rule_id: int, rule: ScheduleRule
    ) -> bool:
        """Replace the pool's block ``rule_id``; ``False`` if the pool has no such block."""
        if await self._counted_pool_row(scope, pool_id) is None:
            return False
        result = await self._session.execute(
            update(PoolStaffingRuleRow)
            .where(PoolStaffingRuleRow.id == rule_id, PoolStaffingRuleRow.pool_id == pool_id)
            .values(**_rule_columns(rule))
        )
        return bool(getattr(result, "rowcount", 0))

    async def delete_pool_rule(self, scope: Scope, pool_id: int, rule_id: int) -> bool:
        """Delete the pool's block ``rule_id``; ``False`` if the pool has no such block."""
        if await self._counted_pool_row(scope, pool_id) is None:
            return False
        result = await self._session.execute(
            delete(PoolStaffingRuleRow).where(
                PoolStaffingRuleRow.id == rule_id, PoolStaffingRuleRow.pool_id == pool_id
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def add_pool_override(
        self, scope: Scope, pool_id: int, override: ScheduleOverride
    ) -> int | None:
        """Insert an exception (a closed day without a window); ``None`` when no such pool."""
        if await self._counted_pool_row(scope, pool_id) is None:
            return None
        row = PoolStaffingOverrideRow(pool_id=pool_id, **_override_columns(override))
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def update_pool_override(
        self, scope: Scope, pool_id: int, override_id: int, override: ScheduleOverride
    ) -> bool:
        """Replace the pool's exception ``override_id``; ``False`` if the pool has none such."""
        if await self._counted_pool_row(scope, pool_id) is None:
            return False
        result = await self._session.execute(
            update(PoolStaffingOverrideRow)
            .where(
                PoolStaffingOverrideRow.id == override_id,
                PoolStaffingOverrideRow.pool_id == pool_id,
            )
            .values(**_override_columns(override))
        )
        return bool(getattr(result, "rowcount", 0))

    async def delete_pool_override(self, scope: Scope, pool_id: int, override_id: int) -> bool:
        """Delete the pool's exception ``override_id``; ``False`` if the pool has none such."""
        if await self._counted_pool_row(scope, pool_id) is None:
            return False
        result = await self._session.execute(
            delete(PoolStaffingOverrideRow).where(
                PoolStaffingOverrideRow.id == override_id,
                PoolStaffingOverrideRow.pool_id == pool_id,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def get_pool_slot(
        self, pool_id: int, group_id: int, start: datetime, occupied_until: datetime
    ) -> Slot | None:
        """Return the pool slot of one service, start and length (the ``uq_slots_pool`` key)."""
        row = await self._session.scalar(
            select(SlotRow).where(
                SlotRow.capacity_pool_id == pool_id,
                SlotRow.group_id == group_id,
                SlotRow.start == start,
                SlotRow.occupied_until == occupied_until,
            )
        )
        if row is None:
            return None
        return Slot(
            row.id,
            row.group_id,
            row.start,
            row.end,
            row.capacity_snapshot,
            BookingKind(row.kind),
            row.capacity_pool_id,
            row.occupied_until,
        )

    async def get_pool_commitments(
        self, pool_id: int, lower: datetime, upper: datetime
    ) -> list[Commitment]:
        """Return the units of the pool held at any moment of ``lower`` to ``upper``.

        One commitment per place taken in a pool slot (design section 2.1): a booking that holds
        one (``("booking", id)``) and an open proposal of a new time (``("proposal", id)``). The
        slot's ``start`` is bounded below by ``lower - MAX_SPAN`` so that the index on
        ``(capacity_pool_id, start)`` limits the scan; ``occupied_until`` then decides.
        """
        window = (
            SlotRow.capacity_pool_id == pool_id,
            SlotRow.start > lower - MAX_SPAN,
            SlotRow.start < upper,
            SlotRow.occupied_until > lower,
        )
        booked = await self._session.execute(
            select(BookingRow.id, SlotRow.start, SlotRow.occupied_until)
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .where(*window, BookingRow.status.in_(_HOLDS_PLACE))
            .order_by(SlotRow.start, BookingRow.id)
        )
        found = [Commitment(("booking", i), s, u) for i, s, u in booked if u is not None]
        owner = aliased(BookingRow)
        held = await self._session.execute(
            select(RescheduleProposalRow.id, SlotRow.start, SlotRow.occupied_until)
            .join(SlotRow, SlotRow.id == RescheduleProposalRow.slot_id)
            .join(owner, owner.id == RescheduleProposalRow.booking_id)
            .where(*window, *open_proposal(owner))
            .order_by(SlotRow.start, RescheduleProposalRow.id)
        )
        proposals = [Commitment(("proposal", i), s, u) for i, s, u in held if u is not None]
        return found + proposals + await open_occupations(self._session, pool_id, upper)


async def read_all_pool_staffing(
    session: AsyncSession,
) -> dict[int, tuple[list[ScheduleRule], list[ScheduleOverride]]]:
    """Return the blocks and exceptions of every pool by pool id, for the configuration export.

    No scope: the export reads the whole installation, as ``read_catalogue`` does, and the
    document service has decided what the caller may export.
    """
    found: dict[int, tuple[list[ScheduleRule], list[ScheduleOverride]]] = {}
    for rule_row in await session.scalars(
        select(PoolStaffingRuleRow).order_by(PoolStaffingRuleRow.id)
    ):
        found.setdefault(rule_row.pool_id, ([], []))[0].append(_rule(rule_row))
    for override_row in await session.scalars(
        select(PoolStaffingOverrideRow).order_by(PoolStaffingOverrideRow.id)
    ):
        found.setdefault(override_row.pool_id, ([], []))[1].append(_override(override_row))
    return found


async def replace_pool_staffing(
    session: AsyncSession,
    pool_id: int,
    rules: list[ScheduleRule] | None,
    overrides: list[ScheduleOverride] | None,
) -> None:
    """Replace a pool's weekly blocks and/or exceptions as a whole; ``None`` leaves that part.

    For the configuration import, which has found the pool by its code inside the organisation
    the document names. The caller has checked the blocks with ``check_pool_staffing``.
    """
    if rules is not None:
        await session.execute(
            delete(PoolStaffingRuleRow).where(PoolStaffingRuleRow.pool_id == pool_id)
        )
        session.add_all(PoolStaffingRuleRow(pool_id=pool_id, **_rule_columns(r)) for r in rules)
    if overrides is not None:
        await session.execute(
            delete(PoolStaffingOverrideRow).where(PoolStaffingOverrideRow.pool_id == pool_id)
        )
        session.add_all(
            PoolStaffingOverrideRow(pool_id=pool_id, **_override_columns(o)) for o in overrides
        )
