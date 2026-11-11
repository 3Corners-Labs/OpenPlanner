"""Staffing of a counted capacity pool, as UTC segments (ADR 0122, design sections 1 and 2.2).

A counted pool has its own weekly blocks and per-date exceptions, shaped exactly like a
service's staffing: ``ScheduleRule``, ``ScheduleOverride`` and ``TimeWindow`` are reused, and
``TimeWindow.capacity`` means *units* (people counted at that moment). The blocks are local to
the pool's time zone; everything that is compared or stored is UTC.

    PoolStaffing (rules, overrides, zone) + closed days (holidays)
          │  windows_on(day)       an exception on a date replaces the rules of that date
          ▼
    Segment(start, end, units, offer)   in UTC, sorted, empty and 0-unit blocks left out
          │
          ▼
    Supply.units_at(t, intake)          what pool_accounting measures commitments against

Daylight saving time follows ``availability.slots_on``: a local time that does not exist
(spring forward) is read with the offset from before the change, so it falls one hour later;
an ambiguous local time (fall back) takes its first occurrence. A block that lies wholly in
the missing hour vanishes; a block that spans the repeated hour lasts one hour longer.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import (
    Offer,
    ScheduleOverride,
    ScheduleRule,
    validate_schedule,
    windows_on,
)


class Intake(StrEnum):
    """What a unit is wanted for (decision P7: a block offers appointments, on demand, or both)."""

    APPOINTMENT = "appointment"
    """Work that was booked for a time in advance."""
    ON_DEMAND = "on_demand"
    """Work without an appointment (a start now)."""


@dataclass(frozen=True)
class Segment:
    """A stretch of UTC time in which ``units`` people are staffed.

    Attributes:
        start: First instant of the segment (UTC).
        end: First instant after it; always later than ``start``.
        units: People counted, 1 or more (a 0-unit block is not a segment).
        offer: What the block offers (ADR 0072); on a pool ``scheduled`` means appointments
            only, ``direct`` without appointment only.
    """

    start: datetime
    end: datetime
    units: int
    offer: Offer = Offer.SCHEDULED

    def covers(self, moment: datetime) -> bool:
        """Return whether ``moment`` lies in ``start <= moment < end``."""
        return self.start <= moment < self.end

    def units_for(self, intake: Intake | None) -> int:
        """Return the units this segment counts for ``intake``.

        ``None`` counts the people regardless of what the block offers (used to judge a
        shortage); an intake the block does not offer counts 0.
        """
        if intake is Intake.APPOINTMENT and not self.offer.scheduled:
            return 0
        if intake is Intake.ON_DEMAND and not self.offer.direct:
            return 0
        return self.units


@dataclass(frozen=True)
class Supply:
    """The staffed segments of a pool, sorted by start; time outside them has 0 units."""

    segments: tuple[Segment, ...] = ()

    def units_at(self, moment: datetime, intake: Intake | None = None) -> int:
        """Return the units staffed at ``moment`` for ``intake``.

        A segment starts at its first instant and ends before its last, so at the border of
        two touching segments the later one counts. Example: 09:00–12:00 with 2 units gives 2
        at 09:00 and 0 at 12:00.
        """
        for segment in self.segments:
            if segment.covers(moment):
                return segment.units_for(intake)
        return 0

    def borders_between(self, lower: datetime, upper: datetime) -> set[datetime]:
        """Return every segment start and end strictly between ``lower`` and ``upper``."""
        borders = {s.start for s in self.segments} | {s.end for s in self.segments}
        return {b for b in borders if lower < b < upper}


@dataclass(frozen=True)
class PoolStaffing:
    """The weekly blocks and the exceptions of one counted pool, in the pool's time zone."""

    timezone: ZoneInfo
    rules: tuple[ScheduleRule, ...] = ()
    overrides: tuple[ScheduleOverride, ...] = ()


def check_pool_staffing(staffing: PoolStaffing) -> None:
    """Validate a pool's blocks: no overlap on any date, and no subjects.

    Subjects belong to a service (ADR 0081), and a pool serves several services.

    Raises:
        DomainError: ``invalid_configuration`` with field ``schedule_rules``,
            ``schedule_overrides`` (overlap) or ``subjects``.
    """
    validate_schedule(staffing.rules, staffing.overrides)
    windows = [r.window for r in staffing.rules]
    windows += [o.window for o in staffing.overrides if o.window is not None]
    if any(window.subjects for window in windows):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "subjects")


def with_closed_days(
    overrides: Iterable[ScheduleOverride], closed_days: Collection[date]
) -> list[ScheduleOverride]:
    """Return ``overrides`` plus a closed override for every closed day without an exception.

    The pool-side twin of ``holidays.with_holidays``: an own exception on a holiday wins.
    """
    given = list(overrides)
    excepted = {o.day for o in given}
    return [*given, *(ScheduleOverride(day, None) for day in sorted(set(closed_days) - excepted))]


def _utc(day: date, local: time, zone: ZoneInfo) -> datetime:
    """Return the UTC instant of local time ``local`` on ``day`` (see the module notes on DST)."""
    return datetime.combine(day, local).replace(tzinfo=zone).astimezone(UTC)


def supply_between(
    staffing: PoolStaffing,
    closed_days: Collection[date],
    lower: datetime,
    upper: datetime,
) -> Supply:
    """Return the staffed segments that touch the UTC interval ``lower`` to ``upper``.

    Segments are whole blocks, not clipped to the interval. An exception on a date replaces
    the weekly blocks of that date; a closed day (holiday) without an exception of its own
    closes the date. Blocks with 0 units and blocks that vanish in the missing DST hour leave
    no segment. Touching blocks stay separate segments.

    Args:
        staffing: Blocks and exceptions, in the pool's time zone.
        closed_days: Local dates that are closed (holidays); the pool's own exception wins.
        lower: Start of the interval (UTC, aware).
        upper: End of the interval (UTC, aware), after ``lower``.

    Raises:
        ValueError: If the interval is empty or reversed.
    """
    if lower >= upper:
        raise ValueError("lower must be before upper")
    zone = staffing.timezone
    overrides = with_closed_days(staffing.overrides, closed_days)
    first = lower.astimezone(zone).date()
    last = upper.astimezone(zone).date()
    segments: list[Segment] = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        for window in windows_on(day, staffing.rules, overrides):
            start, end = _utc(day, window.opens, zone), _utc(day, window.closes, zone)
            if window.staffed and start < end and start < upper and lower < end:
                segments.append(Segment(start, end, window.capacity, window.offer))
    return Supply(tuple(sorted(segments, key=lambda s: s.start)))


def lowest_units(supply: Supply, lower: datetime, upper: datetime, intake: Intake | None) -> int:
    """Return the fewest units staffed at any moment of ``lower`` to ``upper``, 0 for a gap."""
    moments = {lower, *supply.borders_between(lower, upper)}
    return min(supply.units_at(m, intake) for m in moments)
