"""The figures of the live wallboard (ADR 0117 point 20, step 13).

A pure function from what the service read to the six tiles and the day's bar strip. It holds no
database, no text and no clock: ``now`` is passed in, rows carry numbers and codes, and the page
turns them into words.

    live queue state     ─► waiting now, longest wait, in a call         (exact, never hidden)
    today's hourly cells ─► appointments today, no-shows so far, failed hand-overs, bars
                                                  (``suppress_line``: a small cell is hidden)
    slots now and next   ─► how full they are                   (hidden below the threshold)

The live counts are operational state without identity: how many wait, how long the first has
waited, how many are in a call. They are shown exactly, and only to a reader who may also work
the queue (``queue.work``, personal data included in that permission); a reader without it gets
the tiles without them and a sentence that says so. Everything from the rollup goes through the
one small-cell rule of ``usage_suppress``; a percentage is shown only when the number of people
behind it is, so a figure never reveals what its parts hide.

Hour bars are the group-local hours of today. The part of the day that has not come yet is the
*planned* part: bookings already in the agenda, shown shaded, so nobody mistakes them for calls
that happened.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.usage import UsageCell, UsageOutcome
from app.domain.usage_report import ReportGroup, headline_count, local_cells, zones_of
from app.domain.usage_suppress import suppress_line

BOOKED_METRIC = "C4"
"""The metric whose cells are the bookings placed in a slot, keyed on the slot's hour."""
OUTCOME_METRIC = "U2"
FAILED_METRIC = "Q8"
METRICS: frozenset[str] = frozenset({BOOKED_METRIC, OUTCOME_METRIC, FAILED_METRIC})
"""The metrics of the wallboard: a cell of any other metric is none of its business."""
DEFAULT_HOURS = (8, 17)
"""The hours the bar strip always shows (first hour, last hour exclusive), so a quiet day has
a strip too; busier hours outside it extend it."""
STALE_AFTER_POLLS = 3
"""Failed polls in a row after which the board says its data is old (ADR 0117 point 20, R11).
A fixed rule: a wallboard that keeps showing old numbers as current misleads a room."""


@dataclass(frozen=True)
class LiveQueue:
    """One group's own queue right now.

    Attributes:
        waiting: Participants nobody picked up yet.
        longest_wait: How long the first of them has waited.
        connected: Participants in a call now.
    """

    waiting: int
    longest_wait: timedelta
    connected: int


@dataclass(frozen=True)
class LiveFigures:
    """The live counts added up over the groups of the board.

    Attributes:
        waiting: Participants waiting now.
        longest_wait_seconds: The longest wait among them, whole seconds.
        connected: Participants in a call now.
        groups: How many of the board's groups have an own queue these counts come from.
        of_groups: How many groups the board covers.
    """

    waiting: int
    longest_wait_seconds: int
    connected: int
    groups: int
    of_groups: int


@dataclass(frozen=True)
class BlockFill:
    """The places of the slots that cover one moment, added up over the groups.

    Attributes:
        booked: Places taken.
        capacity: Places there are.
    """

    booked: int
    capacity: int


@dataclass(frozen=True)
class HourBar:
    """The bookings of one group-local hour of today.

    Attributes:
        hour: The hour, 0 to 23.
        count: The bookings, ``None`` when a small cell.
        planned: True for an hour that has not started: bookings in the agenda, not calls held.
        current: True for the hour that is running.
    """

    hour: int
    count: int | None
    planned: bool
    current: bool


@dataclass(frozen=True)
class Wallboard:
    """Everything one wallboard shows at ``as_of``.

    Attributes:
        as_of: The moment the board was made (UTC): every tile says it.
        day: The group-local day the board is about.
        live: The live counts, ``None`` when the reader may not see them or no group has an own
            queue.
        long_wait: Whether the longest wait is beyond the organisation's alert limit.
        appointments: Bookings in today's slots, ``None`` when a small cell.
        no_shows: No-shows so far today, ``None`` when a small cell.
        block_now_percent: How full the block that is running is, ``None`` when hidden or when no
            slot is running.
        block_next_percent: The same for the next block.
        failed: Failed hand-overs this hour and the one before, ``None`` when a small cell.
        bars: The day's hours.
        rolled_at: When the rollup last ran: the figures from it are as old as that.
        threshold: The small-cell threshold that was applied.
    """

    as_of: datetime
    day: date
    live: LiveFigures | None
    long_wait: bool
    appointments: int | None
    no_shows: int | None
    block_now_percent: int | None
    block_next_percent: int | None
    failed: int | None
    bars: tuple[HourBar, ...]
    rolled_at: datetime | None
    threshold: int


def live_figures(queues: Sequence[LiveQueue], of_groups: int) -> LiveFigures | None:
    """Add the groups' live queues up; ``None`` when no group has an own queue to read."""
    if not queues:
        return None
    return LiveFigures(
        sum(q.waiting for q in queues),
        int(max(q.longest_wait for q in queues).total_seconds()),
        sum(q.connected for q in queues),
        len(queues),
        of_groups,
    )


def over_alert(figures: LiveFigures | None, alert_minutes: int) -> bool:
    """Return whether the longest wait is beyond ``alert_minutes`` (the *long wait* state)."""
    return figures is not None and figures.longest_wait_seconds > alert_minutes * 60


def fill_percent(fill: BlockFill | None, threshold: int) -> int | None:
    """Return how full a block is in whole percent, hidden when its bookings are a small cell.

    A percentage of a handful of bookings is that handful, so it is shown only when the number
    booked is (``headline_count``). An empty block (nothing booked) is shown as zero, and a
    block without places has no percentage.
    """
    if fill is None or fill.capacity <= 0:
        return None
    if fill.booked == 0:
        return 0
    if headline_count(fill.booked, threshold) is None:
        return None
    return min(100, round(100 * fill.booked / fill.capacity))


def _hours(first: int, last: int, now_hour: int) -> range:
    """Return the hours the strip shows: the default span, widened to what the day holds."""
    start = min(DEFAULT_HOURS[0], first, now_hour)
    end = max(DEFAULT_HOURS[1], last + 1, now_hour + 1)
    return range(start, min(end, 24))


def wallboard(
    groups: Sequence[ReportGroup],
    cells: Sequence[UsageCell],
    live: Sequence[LiveQueue],
    block_now: BlockFill | None,
    block_next: BlockFill | None,
    now: datetime,
    threshold: int,
    alert_minutes: int,
    rolled_at: datetime | None,
) -> Wallboard:
    """Return the board at ``now`` from the groups' hourly cells and live state.

    Args:
        groups: The groups the board covers; they decide the day (the first one's zone).
        cells: Hourly cells around today; ones of other metrics or groups are ignored.
        live: The own queues of the groups the reader may see live (possibly none).
        block_now: Places of the slots that cover ``now``, ``None`` without one.
        block_next: Places of the next slots, ``None`` without one.
        now: The moment the board is made.
        threshold: The small-cell threshold of the organisation.
        alert_minutes: The wait after which the board says *long wait*.
        rolled_at: When the rollup last ran.
    """
    zones = zones_of(groups)
    zone = next(iter(zones.values()), ZoneInfo("UTC"))
    local_now = now.astimezone(zone)
    day = local_now.date()
    booked: Counter[int] = Counter()
    no_shows = 0
    failed = 0
    previous_hour = now - timedelta(hours=1)
    for cell, local in local_cells([c for c in cells if c.metric in METRICS], zones, day, day):
        if cell.metric == BOOKED_METRIC:
            booked[local.hour] += cell.count
        elif cell.metric == OUTCOME_METRIC and cell.outcome is UsageOutcome.NO_SHOW:
            no_shows += cell.count
    for cell in cells:
        if (
            cell.metric == FAILED_METRIC
            and cell.group_id in zones
            and previous_hour.replace(minute=0, second=0, microsecond=0) <= cell.bucket_hour
            and cell.bucket_hour <= now
        ):
            failed += cell.count
    hours = _hours(min(booked, default=24), max(booked, default=0), local_now.hour)
    shown = suppress_line([booked.get(h, 0) for h in hours], threshold)
    bars = tuple(
        HourBar(h, count, h > local_now.hour, h == local_now.hour)
        for h, count in zip(hours, shown.cells, strict=True)
    )
    figures = live_figures(live, len(groups))
    return Wallboard(
        now,
        day,
        figures,
        over_alert(figures, alert_minutes),
        headline_count(sum(booked.values()), threshold),
        headline_count(no_shows, threshold),
        fill_percent(block_now, threshold),
        fill_percent(block_next, threshold),
        headline_count(failed, threshold),
        bars,
        rolled_at,
        threshold,
    )
