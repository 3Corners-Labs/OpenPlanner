"""SQL for the utilisation of a counted pool (ADR 0122 step 5; metrics C1 to C3 per pool).

A mixin of ``SqlTransaction``. It reads times only: a pool's name, its blocks and exceptions,
the holidays of its organisation and, per appointment that holds a unit (confirmed, completed or
no show), the start and the end of its slot's occupied time. No column the purge empties
(``privacy.PURGED_FIELDS``) and no booking id leaves it. The pool and the organisation are
filtered by the reader's ``Scope`` through ``scope_filter`` on the organisation, never on a
group: a pool's figures add up services, so a reader of one service sees none.
"""

from datetime import date, tzinfo

from sqlalchemy import select

from app.adapters.db.models import (
    BookingRow,
    CapacityPoolRow,
    HolidayRow,
    OrganisationHolidayCalendarRow,
    OrganisationRow,
    SlotRow,
)
from app.adapters.db.pool_staffing import PoolStaffingQueries
from app.adapters.db.scope import scope_filter
from app.domain.authorization import Scope
from app.domain.booking import BookingStatus
from app.domain.capacity import PoolMode
from app.domain.pool_accounting import MAX_SPAN
from app.domain.usage_report_pools import Interval, PoolInput, PoolInputs, range_bounds

_COUNTS = [
    status.value
    for status in (BookingStatus.CONFIRMED, BookingStatus.COMPLETED, BookingStatus.NO_SHOW)
]
"""The statuses of a booking that counts as booked (metric C1)."""


class UsagePoolQueries(PoolStaffingQueries):
    """The pool report's part of ``SqlTransaction``; needs ``self._session``."""

    async def read_pool_report_inputs(
        self, scope: Scope, organisation_slug: str, first_day: date, last_day: date
    ) -> PoolInputs | None:
        """Return the organisation's counted pools with staffing, closed days and appointments.

        ``None`` when the organisation is missing or its slug is outside ``scope``. Archived
        pools come too (their history stays); a pool without a time zone has no staffing.
        """
        organisation = await self._session.scalar(
            select(OrganisationRow).where(
                OrganisationRow.slug == organisation_slug,
                scope_filter(scope, OrganisationRow.slug, None),
            )
        )
        if organisation is None:
            return None
        pools = await self._session.execute(
            select(CapacityPoolRow.id, CapacityPoolRow.name, CapacityPoolRow.archived_at)
            .where(
                CapacityPoolRow.organisation_id == organisation.id,
                CapacityPoolRow.mode == PoolMode.COUNTED.value,
            )
            .order_by(CapacityPoolRow.name, CapacityPoolRow.id)
        )
        closed = await self._closed_days(organisation.id, first_day, last_day)
        found: list[PoolInput] = []
        for pool_id, name, archived_at in pools.all():
            staffing = await self.get_pool_staffing(scope, pool_id)
            booked = (
                await self._held_intervals(pool_id, staffing.timezone, first_day, last_day)
                if staffing is not None
                else ()
            )
            found.append(
                PoolInput(pool_id, name, archived_at is not None, staffing, closed, booked)
            )
        return PoolInputs(organisation.id, tuple(found))

    async def _closed_days(
        self, organisation_id: int, first_day: date, last_day: date
    ) -> frozenset[date]:
        """Return the holidays of the organisation's calendars in the range, as dates.

        A holiday is a calendar date, and a pool's days are its own local dates, so the range is
        the same dates for every pool whatever its time zone.
        """
        days = await self._session.scalars(
            select(HolidayRow.day)
            .join(
                OrganisationHolidayCalendarRow,
                OrganisationHolidayCalendarRow.calendar_id == HolidayRow.calendar_id,
            )
            .where(
                OrganisationHolidayCalendarRow.organisation_id == organisation_id,
                HolidayRow.day.between(first_day, last_day),
            )
        )
        return frozenset(days)

    async def _held_intervals(
        self, pool_id: int, zone: tzinfo, first_day: date, last_day: date
    ) -> tuple[Interval, ...]:
        """Return start and occupied end of the appointments that hold a unit in the range."""
        lower, upper = range_bounds(zone, first_day, last_day)
        rows = await self._session.execute(
            select(SlotRow.start, SlotRow.occupied_until)
            .join(BookingRow, BookingRow.slot_id == SlotRow.id)
            .where(
                SlotRow.capacity_pool_id == pool_id,
                SlotRow.start > lower - MAX_SPAN,
                SlotRow.start < upper,
                SlotRow.occupied_until > lower,
                BookingRow.status.in_(_COUNTS),
            )
            .order_by(SlotRow.start, BookingRow.id)
        )
        return tuple((start, until) for start, until in rows if until is not None)
