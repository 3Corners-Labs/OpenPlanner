"""The utilisation of a counted pool: metrics C1 to C3 of ADR 0117 per pool (ADR 0122 step 5).

A service's slot carries a capacity snapshot, so the *Capacity* report counts its places per
slot. A counted pool has no snapshot: its supply is its live staffing, and several services
draw on it, so the places of one slot are not the pool's places. The three measures are the
same, with the pool's own unit of capacity, one person for one hour (a *unit-hour*), in place of
a slot:

    C1  utilisation   booked unit-time over staffed unit-time, in whole percent
    C2  fill          pool-hours by how full they were: empty, under half, under full, full
    C3  idle          staffed unit-time nobody booked

*Staffed* is what the pool's blocks and exceptions staff for appointments (a block for work
without appointment adds nothing, holidays close the day), per UTC hour. *Booked* is the time
that confirmed, completed and no-show appointments hold a unit, from the start to the end of the
slot including its wrap-up (``occupied_until``): pending, suspended and cancelled ones hold none
of it (C1 counts the same bookings). Only hours that have ended and are past the closing delay
count, like a service's slot that has been closed, and only the hours the pool is staffed for
appointments: an hour nobody was staffed for is not idle capacity.

    inputs ─► staffed and booked minutes per hour ─► fill class per hour ─► totals ─► hide small

A pool's staffing is read as it is now and the appointments as the rows still hold them
(``booking_row_retention_days``), so the figures of a past range follow today's staffing.
The report is aggregates only: no name, no address, no booking id leaves ``PoolInput``, which
carries times. It adds up no pools, and a pool with a few appointments hides its figures like a
small cell anywhere else (ADR 0117 point 8): a percentage of two people is those two people.

Pure Python, no I/O: ``usage_report_pool_service`` loads the inputs.
"""

from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from app.domain.pool_staffing import Intake, PoolStaffing, supply_between
from app.domain.slot_close import CLOSE_DELAY
from app.domain.usage import hour_of
from app.domain.usage_report import CountRow, CountTable, count_table
from app.domain.usage_rollup import FILL_CLASSES

POOL_RULE = """
A counted pool (ADR 0122) has no capacity snapshot: its supply is its live staffing and several
services draw on it. The *Capacity* screen of the organisation therefore reads C1 to C3 per pool,
from the pool's own staffing instead of from `slot.closed` facts, with a *unit-hour* (one person
for one hour) in place of a slot:

| Metric | Per pool |
|---|---|
| C1 | booked unit-time over staffed unit-time in the hours that count, in whole percent |
| C2 | pool-hours by fill: empty, under half, under full, full |
| C3 | staffed unit-time nobody booked |

Staffed is what the pool's blocks and exceptions staff for appointments (blocks for work without
appointment add nothing; holidays close the day). Booked is the time confirmed, completed and no
show appointments hold a unit, wrap-up included; an hour never holds more than it is staffed for,
so a shortage reads as a full hour. Only hours that ended more than two hours ago count, and only
hours the pool is staffed for appointments. The staffing is today's and the appointments are those
the rows still hold (`booking_row_retention_days`): the range starts no earlier than that. A pool
with fewer appointments than the small-cell threshold shows its staffing only. Only a reader of
the whole organisation sees the pools; they are not exported.
"""
"""The rule of ``pool_report`` in words, as published in ``docs/reporting.md``."""

HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)

Interval = tuple[datetime, datetime]
"""A start and an end in UTC: the time one appointment holds a unit."""


@dataclass(frozen=True)
class PoolInput:
    """A counted pool as the report needs it: its name, its staffing and times, nothing else.

    Attributes:
        pool_id: The pool's row id.
        name: The pool's name.
        archived: Whether the pool is archived (it stays while it has history).
        staffing: Weekly blocks and exceptions in the pool's time zone; ``None`` for a pool
            that has no time zone yet, so no staffing at all.
        closed_days: The pool-local dates closed by the organisation's holiday calendars.
        booked: The interval of every confirmed, completed or no-show appointment that touches
            the range; no booking id, no status, no service.
    """

    pool_id: int
    name: str
    archived: bool
    staffing: PoolStaffing | None
    closed_days: frozenset[date]
    booked: tuple[Interval, ...]


@dataclass(frozen=True)
class PoolInputs:
    """The counted pools of one organisation, as the store reads them.

    Attributes:
        organisation_id: The organisation, for its own small-cell threshold.
        pools: Its counted pools.
    """

    organisation_id: int
    pools: tuple[PoolInput, ...]


def range_bounds(zone: tzinfo, first_day: date, last_day: date) -> tuple[datetime, datetime]:
    """Return the UTC instants where ``first_day`` begins and the day after ``last_day`` begins.

    Days are the pool's own local days, so the store and the report cut the range alike.
    """
    return (
        datetime.combine(first_day, time.min, zone),
        datetime.combine(last_day + timedelta(days=1), time.min, zone),
    )


@dataclass(frozen=True)
class PoolUtilisation:
    """The three measures of one pool, small cells hidden.

    Attributes:
        pool_id: The pool.
        name: Its name.
        archived: Whether it is archived.
        staffed_minutes: Unit-minutes staffed for appointments in the hours that count; the
            pool's own configuration, shown whatever the appointments.
        appointments: Appointments that held a unit in those hours, ``None`` when a small cell.
        booked_minutes: Unit-minutes they held, ``None`` when hidden.
        utilisation_percent: C1, ``None`` when hidden or nothing was staffed.
        idle_minutes: C3, ``None`` when hidden.
        fill: C2, the pool-hours per fill class; every cell hidden when the figures are.
    """

    pool_id: int
    name: str
    archived: bool
    staffed_minutes: int
    appointments: int | None
    booked_minutes: int | None
    utilisation_percent: int | None
    idle_minutes: int | None
    fill: CountTable


@dataclass(frozen=True)
class PoolReport:
    """Everything the pools part of the *Capacity* screen shows.

    Attributes:
        first_day: The first pool-local day of the range.
        last_day: The last pool-local day of the range.
        threshold: The small-cell threshold that was applied.
        counted_until: The end of the last hour that counts (now minus the closing delay).
        pools: One row per pool, by name; an archived pool without history is left out.
    """

    first_day: date
    last_day: date
    threshold: int
    counted_until: datetime
    pools: tuple[PoolUtilisation, ...]


def _hours(start: datetime, end: datetime) -> Iterator[tuple[datetime, int]]:
    """Yield each UTC hour of ``start`` to ``end`` with the minutes of it that the span covers."""
    hour = hour_of(start)
    while hour < end:
        covered = min(end, hour + HOUR) - max(start, hour)
        yield hour, covered // MINUTE
        hour += HOUR


def _fill_class(booked: int, staffed: int) -> str:
    """Return how full an hour was (metric C2): the classes of ``FILL_CLASSES``."""
    if booked <= 0:
        return FILL_CLASSES[0]
    if booked >= staffed:
        return FILL_CLASSES[3]
    return FILL_CLASSES[1] if booked * 2 < staffed else FILL_CLASSES[2]


def _staffed(
    pool: PoolInput, first_day: date, last_day: date, until: datetime
) -> Counter[datetime]:
    """Return the unit-minutes staffed for appointments per UTC hour of the range."""
    staffing = pool.staffing
    found: Counter[datetime] = Counter()
    if staffing is None:
        return found
    lower, upper = range_bounds(staffing.timezone, first_day, last_day)
    upper = min(upper, until)
    if lower >= upper:
        return found
    for segment in supply_between(staffing, pool.closed_days, lower, upper).segments:
        units = segment.units_for(Intake.APPOINTMENT)
        start, end = max(segment.start, lower), min(segment.end, upper)
        if units > 0 and start < end:
            for hour, minutes in _hours(start, end):
                found[hour] += units * minutes
    return found


def _held(
    pool: PoolInput, staffed: Counter[datetime], until: datetime
) -> tuple[Counter[datetime], int]:
    """Return the minutes held per staffed hour, and how many appointments held any."""
    held: Counter[datetime] = Counter()
    appointments = 0
    for start, end in pool.booked:
        touched = False
        for hour, minutes in _hours(start, min(end, until)):
            if staffed[hour] > 0:
                held[hour] += minutes
                touched = True
        appointments += touched
    return held, appointments


def _utilisation(
    pool: PoolInput, first_day: date, last_day: date, until: datetime, threshold: int
) -> PoolUtilisation:
    """Return the measures of one pool over the hours that count."""
    staffed = _staffed(pool, first_day, last_day, until)
    held, appointments = _held(pool, staffed, until)
    capacity = sum(staffed.values())
    if 0 < appointments < threshold:
        hidden = CountTable(tuple(CountRow(code, None) for code in FILL_CLASSES), None)
        return PoolUtilisation(
            pool.pool_id, pool.name, pool.archived, capacity, None, None, None, None, hidden
        )
    # An hour holds at most what it is staffed for: a shortage (staffing cut under existing
    # appointments) is on the pool's own page and shows here as a full hour, never above 100 %.
    booked = {hour: min(held[hour], minutes) for hour, minutes in staffed.items()}
    total = sum(booked.values())
    fills = Counter(_fill_class(booked[hour], minutes) for hour, minutes in staffed.items())
    return PoolUtilisation(
        pool.pool_id,
        pool.name,
        pool.archived,
        capacity,
        appointments,
        total,
        round(100 * total / capacity) if capacity > 0 else None,
        capacity - total,
        count_table(fills, FILL_CLASSES, threshold),
    )


def pool_report(
    pools: Sequence[PoolInput],
    first_day: date,
    last_day: date,
    *,
    now: datetime,
    threshold: int,
) -> PoolReport:
    """Build the pools' utilisation over the pool-local days ``first_day`` to ``last_day``.

    Args:
        pools: The counted pools of one organisation, with their staffing and appointments.
        first_day: The first day of the range.
        last_day: The last day of the range.
        now: The current time; hours that have not ended ``CLOSE_DELAY`` ago do not count.
        threshold: The effective small-cell threshold.
    """
    until = hour_of(now - CLOSE_DELAY)
    rows = [_utilisation(pool, first_day, last_day, until, threshold) for pool in pools]
    shown = [
        row
        for row in rows
        if not (row.archived and row.staffed_minutes == 0 and not row.appointments)
    ]
    return PoolReport(
        first_day,
        last_day,
        threshold,
        until,
        tuple(sorted(shown, key=lambda r: (r.name.casefold(), r.pool_id))),
    )
