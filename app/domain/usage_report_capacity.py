"""The *Capacity* report: how full the agenda is and when it is busy (ADR 0117 step 6).

A pure function from the hourly cells of the groups in scope to the tables of metrics C1 to C5:
utilisation and idle capacity per day, the fill of the slots, the busiest weekdays and hours, and
the blocks that were short of staff. It holds no database and no text.

    cells ─► group-local day, weekday and hour ─► ``suppress_*`` ─► ``CapacityReport``

Two rules beyond the small cell (ADR 0117 point 8), both against rebuilding one booking from
adjacent reports: the weekday-by-hour table is offered only over a range of at least seven days,
and only for a group whose own total in the range is at least five times the threshold; a group
below that is left out of the table and counted. A day's utilisation and idle capacity need its
slot count to be shown, and a day with a few bookings in many places reads as hidden, because a
percentage of a small number of people is that number.

With no cells (the slot facts of step 3 are not written yet, or reporting is off) every table
is empty and says so, never an error.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from app.domain.slot_close import SHORTFALL_ACCEPTED, SHORTFALL_OPEN, SHORTFALL_RESOLVED
from app.domain.usage import UsageCell
from app.domain.usage_report import (
    CountTable,
    ReportGroup,
    count_table,
    days_in,
    local_cells,
    zones_of,
)
from app.domain.usage_rollup import FILL_CLASSES
from app.domain.usage_suppress import suppress_line, suppress_table

BOOKED = "booked"
CAPACITY = "capacity"
"""The ``reason_code`` words of the two cells a closed slot feeds to metric C1."""
HOUR_CUT_MIN_DAYS = 7
"""The weekday-by-hour table is offered only over this many days or more (ADR 0117 point 8)."""
GROUP_TOTAL_FACTOR = 5
"""A group is cut by hour only when its total in the range is this many times the threshold."""
METRICS: frozenset[str] = frozenset({"C1", "C2", "C3", "C4", "C5"})
"""The metrics of this screen: a cell of any other metric is none of its business."""
WEEKDAYS = 7
HOURS = 24


@dataclass(frozen=True)
class CapacityDay:
    """The slots of one group-local day that have started.

    Attributes:
        day: The day.
        slots: Slots that started, ``None`` when a small cell.
        utilisation_percent: Booked over capacity in whole percent, ``None`` when hidden.
        idle: Places nobody booked, ``None`` when hidden.
    """

    day: date
    slots: int | None
    utilisation_percent: int | None
    idle: int | None


@dataclass(frozen=True)
class HeatTable:
    """Bookings by local weekday and hour (C4), as a table with a shade and the number.

    Attributes:
        offered: Whether the table is offered at all: a range of at least seven days and at
            least one group that is big enough.
        hours: The hours that have any booking in a group that is cut, in order.
        cells: A count per hour (row) and weekday (column, Monday first), ``None`` hidden.
        levels: A shade 0 to 4 per cell: 0 is hidden or nothing, 4 the largest shown.
        column_totals: Each weekday's total where it may be shown.
        total: The grand total where it may be shown.
        groups_left_out: How many groups were too small to be cut by hour.
    """

    offered: bool
    hours: tuple[int, ...]
    cells: tuple[tuple[int | None, ...], ...]
    levels: tuple[tuple[int, ...], ...]
    column_totals: tuple[int | None, ...]
    total: int | None
    groups_left_out: int


@dataclass(frozen=True)
class CapacityReport:
    """Everything the *Capacity* screen shows, with small cells already hidden.

    Attributes:
        first_day: The first day of the range (group-local).
        last_day: The last day of the range.
        as_of: When the rollup last ran.
        threshold: The small-cell threshold that was applied.
        empty: Whether the range holds no cell of this screen's metrics at all; the page says
            so instead of a table of zeros.
        days: Utilisation, slots and idle capacity per day (C1, C3).
        slots: The slots that started in the range, ``None`` when a small cell.
        utilisation_percent: Booked over capacity in the range, ``None`` when hidden.
        idle: Places nobody booked in the range, ``None`` when hidden.
        fill: Slots by how full they were (C2).
        heat: The busiest weekdays and hours (C4).
        shortfall: Blocks short of staff, per state (C5).
    """

    first_day: date
    last_day: date
    as_of: datetime | None
    threshold: int
    empty: bool
    days: tuple[CapacityDay, ...]
    slots: int | None
    utilisation_percent: int | None
    idle: int | None
    fill: CountTable
    heat: HeatTable
    shortfall: CountTable


def _utilisation(booked: int, capacity: int, slots: int, threshold: int) -> int | None:
    """Return booked over capacity in whole percent, or ``None`` when it may not be shown.

    It is hidden when there is no capacity, and when 1 to ``threshold - 1`` people are behind
    it: a percentage of so few is that few, which is a small cell like any other.
    """
    if capacity <= 0 or slots <= 0 or 0 < booked < threshold:
        return None
    return round(100 * booked / capacity)


def _shade(value: int | None, top: int) -> int:
    """Return the shade 0 to 4 of a shown count against the largest shown count."""
    if not value or top <= 0:
        return 0
    return max(1, min(4, -(-4 * value // top)))


def _heat(
    cells: Sequence[tuple[UsageCell, datetime]],
    groups: Sequence[ReportGroup],
    first_day: date,
    last_day: date,
    threshold: int,
) -> HeatTable:
    """Return the weekday-by-hour table of the bookings placed in the range (metric C4)."""
    empty = HeatTable(False, (), (), (), (None,) * WEEKDAYS, None, 0)
    if (last_day - first_day).days + 1 < HOUR_CUT_MIN_DAYS:
        return empty
    per_group: Counter[int] = Counter()
    grid: dict[int, Counter[tuple[int, int]]] = {}
    for cell, local in cells:
        if cell.metric != "C4":
            continue
        per_group[cell.group_id] += cell.count
        grid.setdefault(cell.group_id, Counter())[(local.hour, local.weekday())] += cell.count
    cut = {g.group_id for g in groups if per_group[g.group_id] >= GROUP_TOTAL_FACTOR * threshold}
    left_out = sum(1 for g in groups if g.group_id not in cut)
    counts: Counter[tuple[int, int]] = Counter()
    for group_id in cut:
        counts.update(grid[group_id])
    hours = tuple(h for h in range(HOURS) if any(counts[(h, d)] for d in range(WEEKDAYS)))
    if not hours:
        return HeatTable(True, (), (), (), (None,) * WEEKDAYS, None, left_out)
    view = suppress_table([[counts[(h, d)] for d in range(WEEKDAYS)] for h in hours], threshold)
    top = max((v for row in view.cells for v in row if v is not None), default=0)
    return HeatTable(
        True,
        hours,
        view.cells,
        tuple(tuple(_shade(v, top) for v in row) for row in view.cells),
        view.col_totals,
        view.grand_total,
        left_out,
    )


def capacity_report(
    cells: Sequence[UsageCell],
    groups: Sequence[ReportGroup],
    first_day: date,
    last_day: date,
    *,
    threshold: int,
    as_of: datetime | None,
) -> CapacityReport:
    """Build the *Capacity* tables from ``cells`` of ``groups`` over the range.

    Only cells of the given groups count, and only those whose hour falls on a day of the range
    in that group's time zone. ``threshold`` is the effective small-cell threshold.
    """
    local = [
        (cell, when)
        for cell, when in local_cells(cells, zones_of(groups), first_day, last_day)
        if cell.metric in METRICS
    ]
    slots: Counter[date] = Counter()
    booked: Counter[date] = Counter()
    capacity: Counter[date] = Counter()
    idle: Counter[date] = Counter()
    fill: Counter[str] = Counter()
    shortfall: Counter[str] = Counter()
    for cell, when in local:
        day = when.date()
        if cell.metric == "C1" and cell.reason_code == CAPACITY:
            slots[day] += cell.count
            capacity[day] += cell.sum_value
        elif cell.metric == "C1" and cell.reason_code == BOOKED:
            booked[day] += cell.sum_value
        elif cell.metric == "C3":
            idle[day] += cell.sum_value
        elif cell.metric == "C2" and cell.reason_code:
            fill[cell.reason_code] += cell.count
        elif cell.metric == "C5" and cell.reason_code:
            shortfall[cell.reason_code] += cell.count
    day_list = days_in(first_day, last_day)
    series = suppress_line([slots[d] for d in day_list], threshold)
    rows = []
    for day, shown in zip(day_list, series.cells, strict=True):
        percent = None
        if shown is not None:
            percent = _utilisation(booked[day], capacity[day], slots[day], threshold)
        rows.append(CapacityDay(day, shown, percent, idle[day] if percent is not None else None))
    total_slots = sum(slots.values())
    total_percent = (
        _utilisation(sum(booked.values()), sum(capacity.values()), total_slots, threshold)
        if series.total is not None
        else None
    )
    shortfall_codes = [SHORTFALL_OPEN, SHORTFALL_ACCEPTED, SHORTFALL_RESOLVED]
    shortfall_codes += sorted(set(shortfall) - set(shortfall_codes))
    return CapacityReport(
        first_day=first_day,
        last_day=last_day,
        as_of=as_of,
        threshold=threshold,
        empty=not local,
        days=tuple(rows),
        slots=series.total,
        utilisation_percent=total_percent,
        idle=sum(idle.values()) if total_percent is not None else None,
        fill=count_table(fill, FILL_CLASSES, threshold),
        heat=_heat(local, groups, first_day, last_day, threshold),
        shortfall=count_table(shortfall, shortfall_codes, threshold),
    )
