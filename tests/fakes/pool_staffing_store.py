"""In-memory staffing of counted pools and pool slots (ADR 0122 step 2).

A mixin of ``InMemoryTransaction`` on top of the skills, pools and flows store. It only *stores*;
the rules (overlaps, subjects) are ``app.domain.pool_staffing.check_pool_staffing``, which the
use case calls. Scope and the "counted pools only" rule are what the SQL adapter does too, pinned
by ``tests/adapters/db/test_pool_staffing.py``, which runs the same cases on both. Its data lives
in the fake's ``State`` (``pool_timezones``, ``pool_max_waiting``, ``pool_rules``,
``pool_overrides``).
"""

from dataclasses import replace
from datetime import date, datetime

from app.domain.authorization import Scope
from app.domain.booking import Slot
from app.domain.capacity import PoolMode, check_max_waiting
from app.domain.errors import DomainError, ErrorCode
from app.domain.pool_accounting import MAX_SPAN, Commitment
from app.domain.pool_staffing import PoolStaffing, check_timezone, zone_or_none
from app.domain.scheduling import ScheduleOverride, ScheduleRule, TimeWindow
from tests.fakes.service_flow_store import ServiceFlowStore


def _no_subjects(window: TimeWindow | None) -> None:
    """Refuse subjects on a pool's window, as the SQL adapter does."""
    if window is not None and window.subjects:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "subjects")


class PoolStaffingStore(ServiceFlowStore):
    """The pool staffing and pool slots' part of the in-memory transaction."""

    async def _counted(self, scope: Scope, pool_id: int) -> bool:
        """Return whether ``pool_id`` is a counted pool inside ``scope``."""
        pool = await self.get_capacity_pool(scope, pool_id)
        return pool is not None and pool.mode is PoolMode.COUNTED

    async def get_pool_staffing(
        self, scope: Scope, pool_id: int, from_day: date | None = None
    ) -> PoolStaffing | None:
        zone = zone_or_none(self.state.pool_timezones.get(pool_id))
        if zone is None or not await self._counted(scope, pool_id):
            return None
        rules = [r for p, r in self.state.pool_rules.values() if p == pool_id]
        rules.sort(key=lambda r: (r.weekday, r.window.opens))
        overrides = [
            o
            for p, o in self.state.pool_overrides.values()
            if p == pool_id and (from_day is None or o.day >= from_day)
        ]
        overrides.sort(key=lambda o: (o.day, o.window.opens if o.window else o.day.min))
        return PoolStaffing(zone, tuple(rules), tuple(overrides))

    async def set_pool_timezone(self, scope: Scope, pool_id: int, timezone: str | None) -> bool:
        if timezone is not None:
            check_timezone(timezone)
        if not await self._counted(scope, pool_id):
            return False
        if timezone is None:
            self.state.pool_timezones.pop(pool_id, None)
        else:
            self.state.pool_timezones[pool_id] = timezone
        return True

    async def get_pool_max_waiting(self, pool_id: int) -> int | None:
        return self.state.pool_max_waiting.get(pool_id)

    async def set_pool_max_waiting(self, scope: Scope, pool_id: int, limit: int | None) -> bool:
        check_max_waiting(limit)
        if not await self._counted(scope, pool_id):
            return False
        if limit is None:
            self.state.pool_max_waiting.pop(pool_id, None)
        else:
            self.state.pool_max_waiting[pool_id] = limit
        return True

    async def add_pool_rule(self, scope: Scope, pool_id: int, rule: ScheduleRule) -> int | None:
        _no_subjects(rule.window)
        if not await self._counted(scope, pool_id):
            return None
        rule_id = self._new_id()
        self.state.pool_rules[rule_id] = (pool_id, replace(rule, id=rule_id))
        return rule_id

    async def update_pool_rule(
        self, scope: Scope, pool_id: int, rule_id: int, rule: ScheduleRule
    ) -> bool:
        _no_subjects(rule.window)
        found = self.state.pool_rules.get(rule_id)
        if found is None or found[0] != pool_id or not await self._counted(scope, pool_id):
            return False
        self.state.pool_rules[rule_id] = (pool_id, replace(rule, id=rule_id))
        return True

    async def delete_pool_rule(self, scope: Scope, pool_id: int, rule_id: int) -> bool:
        found = self.state.pool_rules.get(rule_id)
        if found is None or found[0] != pool_id or not await self._counted(scope, pool_id):
            return False
        del self.state.pool_rules[rule_id]
        return True

    async def add_pool_override(
        self, scope: Scope, pool_id: int, override: ScheduleOverride
    ) -> int | None:
        _no_subjects(override.window)
        if not await self._counted(scope, pool_id):
            return None
        override_id = self._new_id()
        self.state.pool_overrides[override_id] = (pool_id, replace(override, id=override_id))
        return override_id

    async def update_pool_override(
        self, scope: Scope, pool_id: int, override_id: int, override: ScheduleOverride
    ) -> bool:
        _no_subjects(override.window)
        found = self.state.pool_overrides.get(override_id)
        if found is None or found[0] != pool_id or not await self._counted(scope, pool_id):
            return False
        self.state.pool_overrides[override_id] = (pool_id, replace(override, id=override_id))
        return True

    async def delete_pool_override(self, scope: Scope, pool_id: int, override_id: int) -> bool:
        found = self.state.pool_overrides.get(override_id)
        if found is None or found[0] != pool_id or not await self._counted(scope, pool_id):
            return False
        del self.state.pool_overrides[override_id]
        return True

    async def get_pool_slot(
        self, pool_id: int, group_id: int, start: datetime, occupied_until: datetime
    ) -> Slot | None:
        return next(
            (
                s
                for s in self.state.slots.values()
                if s.capacity_pool_id == pool_id
                and s.group_id == group_id
                and s.start == start
                and s.occupied_until == occupied_until
            ),
            None,
        )

    async def get_pool_commitments(
        self, pool_id: int, lower: datetime, upper: datetime
    ) -> list[Commitment]:
        found: list[Commitment] = []
        for slot in sorted(self.state.slots.values(), key=lambda s: (s.start, s.id or 0)):
            until = slot.occupied_until
            if (
                slot.capacity_pool_id != pool_id
                or until is None
                or not lower - MAX_SPAN < slot.start < upper
                or until <= lower
            ):
                continue
            found += [
                Commitment(("booking", b.id or 0), slot.start, until)
                for b in self.state.bookings.values()
                if b.slot_id == slot.id and b.holds_place
            ]
            found += [
                Commitment(("proposal", p.id or 0), slot.start, until)
                for p in self.state.proposals.values()
                if p.slot_id == slot.id
                and p.is_open
                and self.state.bookings[p.booking_id].awaits_rebook
            ]
        return found
