"""The *Volume* report: how many bookings, how they ended, how far ahead (ADR 0117 step 5).

A pure function from the hourly cells of the groups in scope to the tables a screen shows. It
holds no database and no text: rows carry codes (an outcome, a bucket, a day), and the page
turns them into words. Every count goes through the small-cell rules of ``usage_suppress``:
a row of a table with a shown total is complemented, and a total below the threshold is not
shown, so no cell can be recovered by subtraction (abuse case R2).

    cells ─► local day of each group ─► counts per table ─► ``suppress_line`` ─► ``VolumeReport``

The range is whole group-local days; an hour is turned into the day of its own group, so a
report over groups in different time zones adds the days they each call the same date.
"""

from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.domain.booking import BookingStatus
from app.domain.usage import (
    BUCKET_LABELS,
    ActorClass,
    UsageCell,
    UsageOutcome,
)
from app.domain.usage_suppress import suppress_line, suppress_table

PROVISIONAL = timedelta(hours=24)
"""The most recent day is marked provisional: its outcomes are not final yet (ADR 0117 point 6)."""

MAX_DAYS = 400
"""The widest range a screen shows: bounds the query; a setting for exports comes with step 8."""

UNKNOWN = "unknown"
"""The code of an actor the backfill could not name."""


class RangedReport(Protocol):
    """What every report family has: its range, its freshness and the threshold it applied."""

    @property
    def first_day(self) -> date:
        """The first day of the range."""
        ...

    @property
    def last_day(self) -> date:
        """The last day of the range."""
        ...

    @property
    def as_of(self) -> datetime | None:
        """When the rollup last ran."""
        ...

    @property
    def threshold(self) -> int:
        """The small-cell threshold that was applied."""
        ...


@dataclass(frozen=True)
class ReportGroup:
    """A group as the report needs it: no personal data.

    Attributes:
        group_id: The group's row id, as the hourly cells carry it.
        organisation_id: The organisation's row id, for its own threshold.
        slug: The group's slug, for links.
        name: The group's name.
        organisation_slug: The organisation's slug.
        organisation_name: The organisation's name.
        timezone: The group's IANA time zone, which decides where a day begins.
    """

    group_id: int
    organisation_id: int
    slug: str
    name: str
    organisation_slug: str
    organisation_name: str
    timezone: str


@dataclass(frozen=True)
class DayStatusCount:
    """Bookings of one group on one day that ended in one status, from before reporting."""

    group_id: int
    day: date
    status: BookingStatus
    count: int


@dataclass(frozen=True)
class CountRow:
    """One row of a table: a code and its count, or ``None`` when the count is hidden."""

    code: str
    count: int | None


@dataclass(frozen=True)
class CountTable:
    """A table of counts with its total, which is ``None`` when it may not be shown."""

    rows: tuple[CountRow, ...]
    total: int | None


@dataclass(frozen=True)
class DayRow:
    """The bookings created on one group-local day."""

    day: date
    count: int | None
    provisional: bool


@dataclass(frozen=True)
class VolumeReport:
    """Everything the *Volume* screen shows, with small cells already hidden.

    Attributes:
        first_day: The first day of the range (group-local).
        last_day: The last day of the range (group-local).
        as_of: When the rollup last ran, so a reader knows how fresh the figures are.
        threshold: The small-cell threshold that was applied.
        days: Bookings created per day (U1).
        created_total: Their total, hidden below the threshold.
        outcomes: How bookings ended, per outcome (U2).
        lead_time: Bookings created per lead-time bucket (U6), bucket numbers as codes.
        lead_mean_minutes: The mean lead time in whole minutes, when the total may be shown.
        participant_cancellations: Cancellations by participants per bucket of the time left
            before the start (U4, U5); a participant cancels until the start, never after.
        reschedules: Moves per actor (U7).
        confirmation: Confirmed against lapsed unconfirmed bookings (U9).
        conversion_percent: Confirmed share in whole percent, when both cells and the total
            may be shown.
        closure: Bookings suspended and resumed by closures (U10).
        before_reporting: Outcomes of bookings from before reporting, per status.
    """

    first_day: date
    last_day: date
    as_of: datetime | None
    threshold: int
    days: tuple[DayRow, ...]
    created_total: int | None
    outcomes: CountTable
    lead_time: CountTable
    lead_mean_minutes: int | None
    participant_cancellations: CountTable
    reschedules: CountTable
    confirmation: CountTable
    conversion_percent: int | None
    closure: CountTable
    before_reporting: CountTable


def days_in(first: date, last: date) -> list[date]:
    """Return every day from ``first`` to ``last``, inclusive."""
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def created_in_range(
    cells: Sequence[UsageCell], group: ReportGroup, first_day: date, last_day: date
) -> int:
    """Return the bookings created in ``group`` from ``first_day`` to ``last_day`` (U1).

    Days are the group's own. This is the raw sum; a caller shows it through ``headline_count``
    so the small-cell rule is applied in one place.
    """
    zone = ZoneInfo(group.timezone)
    return sum(
        c.count
        for c in cells
        if c.metric == "U1"
        and c.group_id == group.group_id
        and first_day <= c.bucket_hour.astimezone(zone).date() <= last_day
    )


def headline_count(total: int, threshold: int) -> int | None:
    """Return ``total``, or ``None`` when it is a small cell (the one rule, ``suppress_line``)."""
    return suppress_line([total], threshold).cells[0]


def count_table(counts: Counter[str], codes: Sequence[str], threshold: int) -> CountTable:
    """Return ``counts`` for ``codes`` in that order, small cells hidden and complemented."""
    view = suppress_line([counts.get(code, 0) for code in codes], threshold)
    return CountTable(
        tuple(CountRow(code, shown) for code, shown in zip(codes, view.cells, strict=True)),
        view.total,
    )


@dataclass(frozen=True)
class GridTable:
    """A table of two cuts with the totals that may be shown.

    Attributes:
        rows: The row codes.
        columns: The column codes.
        cells: A count per row and column, ``None`` where hidden.
        row_totals: Each row's total, ``None`` where it may not be shown.
        column_totals: Each column's total, likewise.
        total: The grand total, likewise.
    """

    rows: tuple[str, ...]
    columns: tuple[str, ...]
    cells: tuple[tuple[int | None, ...], ...]
    row_totals: tuple[int | None, ...]
    column_totals: tuple[int | None, ...]
    total: int | None


def grid_table(
    counts: Counter[tuple[str, str]], rows: Sequence[str], columns: Sequence[str], threshold: int
) -> GridTable:
    """Return ``counts`` as a two-way table with suppression along both axes."""
    view = suppress_table([[counts.get((r, c), 0) for c in columns] for r in rows], threshold)
    return GridTable(
        tuple(rows), tuple(columns), view.cells, view.row_totals, view.col_totals, view.grand_total
    )


def zones_of(groups: Sequence[ReportGroup]) -> dict[int, ZoneInfo]:
    """Return each group's time zone by group id: where its days and hours begin."""
    return {g.group_id: ZoneInfo(g.timezone) for g in groups}


def local_cells(
    cells: Sequence[UsageCell], zones: dict[int, ZoneInfo], first_day: date, last_day: date
) -> Iterator[tuple[UsageCell, datetime]]:
    """Yield the cells of the given groups whose group-local day lies in the range.

    Each cell comes with its hour in the group's own time zone, from which a caller takes the
    day, the weekday or the hour. A cell of a group that is not in ``zones`` is left out, so a
    report never adds up a group the reader did not ask for.
    """
    for cell in cells:
        zone = zones.get(cell.group_id)
        if zone is None:
            continue
        local = cell.bucket_hour.astimezone(zone)
        if first_day <= local.date() <= last_day:
            yield cell, local


@dataclass(frozen=True)
class Distribution:
    """A duration spread over the fixed buckets, with its exact mean.

    Attributes:
        table: The count per bucket code (``str(code)``), small cells hidden.
        mean_seconds: The exact mean in whole seconds, shown only when the total may be shown.
    """

    table: CountTable
    mean_seconds: int | None


UNSIGNED_BUCKETS: tuple[str, ...] = tuple(str(b) for b in range(len(BUCKET_LABELS)))
"""The bucket codes of a duration: 0 (under a minute) to 9 (over seven days)."""

SIGNED_BUCKETS: tuple[str, ...] = tuple(
    str(b) for b in range(-len(BUCKET_LABELS), len(BUCKET_LABELS))
)
"""The bucket codes of a signed duration, from the earliest (-10) to the latest (9)."""


def distribution(
    cells: Sequence[UsageCell], codes: Sequence[str], threshold: int, *, drop_empty: bool = False
) -> Distribution:
    """Return the buckets of ``cells`` (all of one metric) and the exact mean.

    A bucket is a hidden cell below the threshold; the mean needs the whole table's total to be
    shown, so a mean never points at a small group. With ``drop_empty`` a bucket nobody is in
    leaves the table (the signed set has twenty); the rest is suppressed as usual.
    """
    counts: Counter[str] = Counter()
    seconds = calls = 0
    for cell in cells:
        if cell.bucket is None:
            continue
        counts[str(cell.bucket)] += cell.count
        seconds += cell.sum_value
        calls += cell.count
    wanted = [c for c in codes if counts.get(c, 0) > 0] if drop_empty else list(codes)
    table = count_table(counts, wanted, threshold)
    mean = round(seconds / calls) if table.total is not None and calls else None
    return Distribution(table, mean)


def volume_report(
    cells: Sequence[UsageCell],
    groups: Sequence[ReportGroup],
    first_day: date,
    last_day: date,
    *,
    threshold: int,
    now: datetime,
    as_of: datetime | None,
    before_reporting: Sequence[DayStatusCount] = (),
) -> VolumeReport:
    """Build the *Volume* tables from ``cells`` of ``groups`` over ``first_day`` to ``last_day``.

    Only cells of the given groups count, and only those whose hour falls on a day of the range
    in that group's time zone. ``threshold`` is the effective small-cell threshold
    (``usage_suppress.effective_threshold``); it is never below the floor of 3.
    """
    zones = {g.group_id: ZoneInfo(g.timezone) for g in groups}
    created: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    lead: Counter[str] = Counter()
    lead_seconds = lead_count = 0
    cancels: Counter[str] = Counter()
    moved: Counter[str] = Counter()
    confirm: Counter[str] = Counter()
    closure: Counter[str] = Counter()
    for cell in cells:
        zone = zones.get(cell.group_id)
        if zone is None:
            continue
        day = cell.bucket_hour.astimezone(zone).date()
        if not first_day <= day <= last_day:
            continue
        if cell.metric == "U1":
            created[day.isoformat()] += cell.count
        elif cell.metric == "U2" and cell.outcome is not None:
            outcomes[cell.outcome.value] += cell.count
        elif cell.metric == "U6" and cell.bucket is not None:
            lead[str(cell.bucket)] += cell.count
            lead_seconds += cell.sum_value
            lead_count += cell.count
        elif cell.metric == "U5" and cell.bucket is not None:
            cancels[str(cell.bucket)] += cell.count
        elif cell.metric == "U7":
            actor = cell.actor_class.value if cell.actor_class is not None else UNKNOWN
            moved[actor] += cell.count
        elif cell.metric == "U9":
            confirm["confirmed" if cell.outcome is None else cell.outcome.value] += cell.count
        elif cell.metric == "U10" and cell.reason_code is not None:
            closure[cell.reason_code] += cell.count
    day_list = days_in(first_day, last_day)
    series = suppress_line([created.get(d.isoformat(), 0) for d in day_list], threshold)
    last_provisional = (now - PROVISIONAL).date()
    days = tuple(
        DayRow(d, shown, d >= last_provisional)
        for d, shown in zip(day_list, series.cells, strict=True)
    )
    lead_table = count_table(lead, [str(b) for b in range(len(BUCKET_LABELS))], threshold)
    mean = (
        round(lead_seconds / lead_count / 60)
        if lead_table.total is not None and lead_count
        else None
    )
    confirmation = count_table(
        confirm, ["confirmed", UsageOutcome.EXPIRED_UNCONFIRMED.value], threshold
    )
    confirmed_shown, lapsed_shown = (r.count for r in confirmation.rows)
    conversion = None
    if (
        confirmation.total is not None
        and confirmed_shown is not None
        and lapsed_shown is not None
        and confirmed_shown + lapsed_shown > 0
    ):
        conversion = round(100 * confirmed_shown / (confirmed_shown + lapsed_shown))
    statuses: Counter[str] = Counter()
    for row in before_reporting:
        if first_day <= row.day <= last_day and row.group_id in zones:
            statuses[row.status.value] += row.count
    return VolumeReport(
        first_day=first_day,
        last_day=last_day,
        as_of=as_of,
        threshold=threshold,
        days=days,
        created_total=series.total,
        outcomes=count_table(outcomes, [o.value for o in UsageOutcome], threshold),
        lead_time=lead_table,
        lead_mean_minutes=mean,
        participant_cancellations=count_table(
            cancels, [str(b) for b in range(len(BUCKET_LABELS))], threshold
        ),
        reschedules=count_table(moved, [*(a.value for a in ActorClass), UNKNOWN], threshold),
        confirmation=confirmation,
        conversion_percent=conversion,
        closure=count_table(closure, ["suspended", "resumed"], threshold),
        before_reporting=count_table(statuses, [s.value for s in BookingStatus], threshold),
    )
