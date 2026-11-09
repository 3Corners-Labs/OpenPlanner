"""A usage report as the rows of a CSV: exactly its tables, small cells already hidden (ADR 0117).

The screen's tables come from a report object whose cells below the threshold are ``None``
(point 8). This module only walks that object, so the file shows nothing the screen does not and
the suppression is the same code, not a second rule. The layout is long, so every family fits one
file whatever its tables look like:

    table ; row ; column ; value ; note

``table`` is the report field the table has (``mix``, ``task_wait``, ``days``); ``row`` and
``column`` are the codes the screen labels (a day, a bucket, an outcome, a weekday); ``value`` is
the count, percent or seconds; ``note`` is ``hidden`` where the screen shows ``< n`` or nothing
(the value is then empty). A reader's tool reads the labels from ``docs/reporting.md``,
in the section "CSV export"; the file carries no interface text, so the language does not change it.

    report ─► ``screen_rows`` ─► (table, row, column, value, note) …
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, date
from typing import Any

from app.domain.csv_export import Cell
from app.domain.usage import UsageCell
from app.domain.usage_report import CountTable, GridTable, ReportGroup, local_cells, zones_of
from app.domain.usage_report_capacity import HeatTable
from app.domain.usage_suppress import show_cell

SCREEN_HEADER: tuple[str, ...] = ("table", "row", "column", "value", "note")
"""The columns of every screen CSV."""

HIDDEN = "hidden"
"""The note of a figure the screen does not show: a small cell, or a total that would reveal one."""

_NOT_TABLES = frozenset({"first_day", "last_day", "as_of", "threshold", "empty"})
"""Report fields that say what the range is, not a figure; the file's name and audit event do."""

_PRESENTATION = frozenset({"levels", "offered", "groups_left_out", "locales_groups_left_out"})
"""Fields that only draw or explain the screen (a shade, whether a table is offered)."""

type Row = tuple[str, str, str, Cell, str]
"""One line of the file: table, row, column, value, note."""

_EVERY_WEEKDAY = range(7)


def _figure(table: str, row: str, column: str, value: Cell) -> Row:
    """Return one line: the figure, or an empty value with the note ``hidden`` for ``None``."""
    return (table, row, column, value, HIDDEN if value is None else "")


def _count_table(name: str, table: CountTable) -> Iterator[Row]:
    """Yield a table of counts per code, then its total."""
    for row in table.rows:
        yield _figure(name, row.code, "count", row.count)
    yield _figure(name, "total", "count", table.total)


def _grid(name: str, grid: GridTable) -> Iterator[Row]:
    """Yield a grid cell by cell, then the totals the screen shows with it."""
    for row, cells, row_total in zip(grid.rows, grid.cells, grid.row_totals, strict=True):
        for column, cell in zip(grid.columns, cells, strict=True):
            yield _figure(name, row, column, cell)
        yield _figure(name, row, "total", row_total)
    for column, column_total in zip(grid.columns, grid.column_totals, strict=True):
        yield _figure(name, "total", column, column_total)
    yield _figure(name, "total", "total", grid.total)


def _heat(name: str, heat: HeatTable) -> Iterator[Row]:
    """Yield the hour by weekday table when the screen offers it; nothing otherwise."""
    if not heat.offered:
        return
    for hour, cells in zip(heat.hours, heat.cells, strict=True):
        for weekday, cell in zip(_EVERY_WEEKDAY, cells, strict=True):
            yield _figure(name, f"{hour:02d}", str(weekday), cell)
    for weekday, total in zip(_EVERY_WEEKDAY, heat.column_totals, strict=True):
        yield _figure(name, "total", str(weekday), total)
    yield _figure(name, "total", "total", heat.total)


def _per_day(name: str, days: tuple[object, ...]) -> Iterator[Row]:
    """Yield one line per figure of each day of a day table (``DayRow``, ``DayPeak``, …)."""
    for day in days:
        if not is_dataclass(day):
            raise TypeError(f"day table {name} holds something that is no dataclass")
        values = {f.name: getattr(day, f.name) for f in fields(day)}
        label = values.pop("day").isoformat()
        for column, value in values.items():
            if column not in _PRESENTATION and not isinstance(value, bool):
                yield _figure(name, label, column, value)


def _walk(name: str, value: object) -> Iterator[Row]:
    """Yield the lines of one report field, by what it holds."""
    if isinstance(value, CountTable):
        yield from _count_table(name, value)
    elif isinstance(value, GridTable):
        yield from _grid(name, value)
    elif isinstance(value, HeatTable):
        yield from _heat(name, value)
    elif isinstance(value, tuple) and value and is_dataclass(value[0]):
        yield from _per_day(name, value)
    elif is_dataclass(value):
        for inner in fields(value):
            yield from _walk(f"{name}.{inner.name}", getattr(value, inner.name))
    elif isinstance(value, int | None) and not isinstance(value, bool):
        yield _figure(name, "", "", value)
    elif value != ():
        raise TypeError(f"report field {name} has a type the export does not know")


def screen_rows(report: object) -> list[Row]:
    """Return every table of ``report`` as lines, in the order the screen shows them.

    Args:
        report: A report of one family (``VolumeReport``, ``QueueReport``, ``CapacityReport``
            or ``ChannelsReport``); its small cells are already hidden.
    """
    if not is_dataclass(report):
        raise TypeError("a report is a dataclass")
    rows: list[Row] = []
    for field in fields(report):
        if field.name in _NOT_TABLES | _PRESENTATION:
            continue
        value = getattr(report, field.name)
        if isinstance(value, date):
            continue
        rows.extend(_walk(field.name, value))
    return rows


TIDY_VERSION = "openplanner.usage/1"
"""The name and version of the tidy file; the version changes only with an ADR amendment."""

TIDY_HEADER: tuple[str, ...] = (
    "hour",
    "organisation",
    "group",
    "metric",
    "booking_kind",
    "dispatch_kind",
    "subject",
    "locale",
    "channel",
    "outcome",
    "actor_class",
    "reason",
    "bucket",
    "count",
    "sum",
    "max",
)
"""The columns of ``openplanner.usage/1``: hour (UTC), who, metric, the cuts, then the figures."""


CUT_FIELDS: dict[str, str] = {
    "booking_kind": "booking_kind",
    "dispatch_kind": "dispatch_kind",
    "subject": "subject_code",
    "locale": "locale",
    "channel": "channel_kind",
    "outcome": "outcome",
    "actor_class": "actor_class",
    "reason": "reason_code",
}
"""The cut columns of the tidy file and the cell field each one reads; the API's ``cut`` is one."""


@dataclass(frozen=True)
class Tidy:
    """The rows of a tidy file and how many cells were left out.

    Attributes:
        rows: One row per hourly cell that may be shown.
        hidden: How many cells were below the threshold and so are not in the file at all, so
            the file does not even say which cuts a small cell had.
    """

    rows: list[tuple[Cell, ...]]
    hidden: int


def tidy_rows(
    cells: Sequence[UsageCell],
    groups: Sequence[ReportGroup],
    first_day: date,
    last_day: date,
    threshold: int,
    keep: frozenset[str] | None = None,
    metric: str | None = None,
) -> Tidy:
    """Return one row per hourly cell of ``groups`` in the range, small cells left out.

    ``keep`` names the cut columns to keep (keys of ``CUT_FIELDS``); the cells are added up over
    the others before the threshold is applied, as the screens add up before they hide. The
    duration bucket is always kept. ``None`` keeps every cut as stored. ``metric`` limits the
    rows to one metric.

    The range is whole group-local days, as on the screens. A cell below ``threshold`` is not
    written (a row would say that something with those cuts happened); there are no totals, so
    nothing is left to subtract. Rows are sorted by hour, group, metric and cuts, so two exports
    of the same range are the same file.
    """
    by_id = {g.group_id: g for g in groups}
    shown: list[tuple[Cell, ...]] = []
    hidden = 0
    wanted = [c for c, _local in local_cells(cells, zones_of(groups), first_day, last_day)]
    if metric is not None:
        wanted = [c for c in wanted if c.metric == metric]
    for cell in _added_up(wanted, keep):
        figure = show_cell(cell, threshold)
        if figure.suppressed:
            hidden += 1
            continue
        group = by_id[cell.group_id]
        shown.append(
            (
                cell.bucket_hour.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                group.organisation_slug,
                group.slug,
                cell.metric,
                *(_code(c) for c in _cuts(cell)),
                figure.count,
                figure.sum_value,
                figure.max_value,
            )
        )
    return Tidy(sorted(shown, key=row_key), hidden)


KEY_COLUMNS = 13
"""The leading columns of a tidy row that identify its cell; the figures follow."""


def row_key(row: Sequence[Cell]) -> tuple[str, ...]:
    """Return the sort key of a tidy row: its identifying columns as text (empty for none)."""
    return tuple("" if c is None else str(c) for c in row[:KEY_COLUMNS])


def _added_up(cells: Sequence[UsageCell], keep: frozenset[str] | None) -> list[UsageCell]:
    """Return ``cells`` with the cuts not in ``keep`` removed and equal cells added together."""
    if keep is None:
        return list(cells)
    blank: dict[str, Any] = {f: None for n, f in CUT_FIELDS.items() if n not in keep}
    merged: dict[tuple[object, ...], UsageCell] = {}
    for cell in cells:
        cut = replace(cell, **blank)
        key = (cut.bucket_hour, cut.group_id, cut.metric, *_cuts(cut))
        found = merged.get(key)
        merged[key] = (
            cut
            if found is None
            else replace(
                found,
                count=found.count + cut.count,
                sum_value=found.sum_value + cut.sum_value,
                max_value=max(found.max_value, cut.max_value),
            )
        )
    return list(merged.values())


def _cuts(cell: UsageCell) -> tuple[object, ...]:
    """Return the cut codes of a cell in the file's column order."""
    return (
        cell.booking_kind,
        cell.dispatch_kind,
        cell.subject_code,
        cell.locale,
        cell.channel_kind,
        cell.outcome,
        cell.actor_class,
        cell.reason_code,
        cell.bucket,
    )


def _code(value: object) -> Cell:
    """Return a cut as its code: the value of an enum, the text or number itself, or empty."""
    if value is None:
        return None
    inner = getattr(value, "value", value)
    return inner if isinstance(inner, str | int) else str(inner)
