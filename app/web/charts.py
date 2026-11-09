"""Server-rendered SVG charts for the overview widgets: geometry and text, no markup (ADR 0061 §5).

A chart is built in two steps. This module turns a list of ``Datum`` into plain geometry
(rectangles, points, ring slices, label positions) and into the sentence a screen reader
gets; the template ``admin/widgets/_chart.html`` turns the geometry into SVG. No chart
library, no script, no inline style: every colour comes from a CSS class (``chart-series-N``,
palette in ``admin.css``), so colour is never the only signal. Each chart carries its values as
text (bars and points print their value, a pie has a legend with value and share), a text
summary with every value for ``aria-label``, and the same data as a table behind a
``<details>`` (the template).

    bar    ─► vertical up to ``VERTICAL_BAR_LIMIT`` items, horizontal above it
    line   ─► one polyline over the items, a marker and a value on each point
    pie    ─► a ring; a slice per item with a value above zero, a legend with all of them

All sizes are design values in SVG user units, fixed on purpose (ADR 0042: not tunable).
Pure Python, no framework imports; the labels arrive already translated.

The usage reports (ADR 0117 point 10) add a second family at the end of this module, with the
same split of work: ``report_geometry`` turns a ``ReportChart`` (column, stacked column or line
over categories, hidden cells as ``None``) into geometry, and the macro
``admin/_report_chart.html`` draws it. See the section "Report charts" below.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

VERTICAL_BAR_LIMIT = 4
"""Up to this many items a bar chart stands upright; above it the bars lie down (ADR 0061 §5)."""

SERIES_CLASSES = 6
"""How many ``chart-series-N`` colours ``admin.css`` defines; the palette repeats after that."""

LABEL_LIMIT = 16
"""Longest label drawn inside the SVG; longer ones end in an ellipsis (the table has them whole)."""

ELLIPSIS = "…"

BAR_WIDTH = 320
BAR_HEIGHT = 168
ROW_HEIGHT = 28
LABEL_AREA = 112
VALUE_AREA = 40
PLOT_TOP = 18
AXIS_BOTTOM = 26
LINE_LEFT = 28
LINE_RIGHT = 30
PIE_SIZE = 140
PIE_OUTER = 62
PIE_INNER = 36


class ChartKind(StrEnum):
    """The shapes a widget may draw."""

    BAR = "bar"
    LINE = "line"
    PIE = "pie"


@dataclass(frozen=True)
class Datum:
    """One value to draw: a translated label and a count.

    Attributes:
        label: What the value is about, e.g. a group name; already translated.
        value: A non-negative count or duration; the chart prints it as a whole number.
    """

    label: str
    value: int


@dataclass(frozen=True)
class Mark:
    """One drawn item: where it sits and which text it carries.

    Attributes:
        series: The ``chart-series-N`` colour index (never the only signal: ``value_text``).
        label: The item's label as drawn (shortened); ``full_label`` is the whole.
        full_label: The item's whole label, for the ``<title>`` and the table.
        value_text: The value as printed on the mark.
        x: Left edge (bar), centre (line point), or label anchor (pie: unused).
        y: Top edge (bar) or centre (line point).
        width: Bar width, zero for the others.
        height: Bar height, zero for the others.
        label_x: Where the category label is anchored.
        label_y: Baseline of the category label.
        value_x: Where the value text is anchored.
        value_y: Baseline of the value text.
        value_anchor: ``start``, ``middle`` or ``end``, the SVG ``text-anchor`` of the value.
        label_anchor: The same for the category label.
        path: The ring slice's path data (pie only).
        share: The item's share of the total, as a whole percentage (pie only).
    """

    series: int
    label: str
    full_label: str
    value_text: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    label_x: float = 0.0
    label_y: float = 0.0
    value_x: float = 0.0
    value_y: float = 0.0
    value_anchor: str = "middle"
    label_anchor: str = "middle"
    path: str = ""
    share: int = 0


@dataclass(frozen=True)
class Chart:
    """A chart as geometry: the kind, the canvas and the marks.

    Attributes:
        kind: Bar, line or pie.
        orientation: ``vertical`` or ``horizontal`` for a bar chart, empty otherwise.
        width: The SVG ``viewBox`` width.
        height: The SVG ``viewBox`` height.
        marks: One per datum, in order.
        points: The polyline's ``points`` attribute (line only).
        axis: The axis line as ``(x1, y1, x2, y2)``, or ``None`` (pie).
        total: The sum of the values (printed in the middle of a pie).
        total_text: The same as printed text.
    """

    kind: ChartKind
    orientation: str
    width: int
    height: int
    marks: tuple[Mark, ...]
    points: str = ""
    axis: tuple[float, float, float, float] | None = None
    total: int = 0
    total_text: str = ""


def shorten(label: str, limit: int = LABEL_LIMIT) -> str:
    """Return ``label`` cut to ``limit`` characters with an ellipsis when it was longer."""
    return label if len(label) <= limit else label[: limit - 1].rstrip() + ELLIPSIS


def series_of(index: int) -> int:
    """Return the colour index of the ``index``-th item; the palette repeats."""
    return index % SERIES_CLASSES


def describe(title: str, data: list[Datum], item: Callable[[Datum], str], joiner: str) -> str:
    """Return the text summary: the title, then every value.

    ``item`` writes one value as a translated sentence (e.g. "Balie: 4 waiting"), ``joiner``
    is what separates two values (e.g. ``"; "``). The summary goes in the SVG's ``aria-label``
    and names every value, so nothing depends on seeing the picture.
    """
    return f"{title}: {joiner.join(item(d) for d in data)}"


def _scale(value: int, top: int) -> float:
    """Return ``value`` as a fraction of ``top`` (a chart of only zeros has no height)."""
    return value / top if top > 0 else 0.0


def bar_chart(data: list[Datum]) -> Chart:
    """Return a bar chart: upright for up to four items, lying down for more."""
    if len(data) > VERTICAL_BAR_LIMIT:
        return _horizontal(data)
    return _vertical(data)


def _vertical(data: list[Datum]) -> Chart:
    """Upright bars with the value above each bar and the label below the axis."""
    top = max((d.value for d in data), default=0)
    axis_y = BAR_HEIGHT - AXIS_BOTTOM
    plot = axis_y - PLOT_TOP - 14
    slot = BAR_WIDTH / max(len(data), 1)
    marks = []
    for index, datum in enumerate(data):
        height = round(plot * _scale(datum.value, top), 1)
        width = round(slot * 0.6, 1)
        x = round(index * slot + (slot - width) / 2, 1)
        centre = round(x + width / 2, 1)
        marks.append(
            Mark(
                series_of(index),
                shorten(datum.label, 12),
                datum.label,
                str(datum.value),
                x=x,
                y=round(axis_y - height, 1),
                width=width,
                height=height,
                label_x=centre,
                label_y=BAR_HEIGHT - 8,
                value_x=centre,
                value_y=round(axis_y - height - 5, 1),
            )
        )
    return Chart(
        ChartKind.BAR,
        "vertical",
        BAR_WIDTH,
        BAR_HEIGHT,
        tuple(marks),
        axis=(0, axis_y, BAR_WIDTH, axis_y),
    )


def _horizontal(data: list[Datum]) -> Chart:
    """Lying bars: the label on the left, the bar, the value at its end."""
    top = max((d.value for d in data), default=0)
    plot = BAR_WIDTH - LABEL_AREA - VALUE_AREA
    marks = []
    for index, datum in enumerate(data):
        width = round(plot * _scale(datum.value, top), 1)
        y = index * ROW_HEIGHT + 4
        middle = y + 9
        marks.append(
            Mark(
                series_of(index),
                shorten(datum.label),
                datum.label,
                str(datum.value),
                x=LABEL_AREA,
                y=y,
                width=width,
                height=14,
                label_x=LABEL_AREA - 8,
                label_y=middle + 4,
                label_anchor="end",
                value_x=round(LABEL_AREA + width + 6, 1),
                value_y=middle + 4,
                value_anchor="start",
            )
        )
    height = len(data) * ROW_HEIGHT + 8
    return Chart(
        ChartKind.BAR,
        "horizontal",
        BAR_WIDTH,
        height,
        tuple(marks),
        axis=(LABEL_AREA, 0, LABEL_AREA, height),
    )


def line_chart(data: list[Datum]) -> Chart:
    """Return a line over the items, in order, with a marker and the value at each point."""
    top = max((d.value for d in data), default=0)
    axis_y = BAR_HEIGHT - AXIS_BOTTOM
    plot = axis_y - PLOT_TOP - 14
    span = BAR_WIDTH - LINE_LEFT - LINE_RIGHT
    steps = max(len(data) - 1, 1)
    marks = []
    for index, datum in enumerate(data):
        x = round(LINE_LEFT + (span * index / steps if len(data) > 1 else span / 2), 1)
        y = round(axis_y - plot * _scale(datum.value, top), 1)
        marks.append(
            Mark(
                0,
                shorten(datum.label, 10),
                datum.label,
                str(datum.value),
                x=x,
                y=y,
                label_x=x,
                label_y=BAR_HEIGHT - 8,
                value_x=x,
                value_y=round(y - 8, 1),
            )
        )
    points = " ".join(f"{m.x},{m.y}" for m in marks)
    return Chart(
        ChartKind.LINE,
        "",
        BAR_WIDTH,
        BAR_HEIGHT,
        tuple(marks),
        points,
        (LINE_LEFT, axis_y, BAR_WIDTH - LINE_RIGHT, axis_y),
    )


def _point(centre: float, radius: float, angle: float) -> tuple[float, float]:
    """Return the point on a circle at ``angle`` radians, 0 at twelve o'clock, clockwise."""
    return (
        round(centre + radius * math.sin(angle), 2),
        round(centre - radius * math.cos(angle), 2),
    )


def _slice(start: float, end: float) -> str:
    """Return the path data of the ring slice between two angles (a full ring as two halves)."""
    centre = PIE_SIZE / 2
    if end - start >= 2 * math.pi - 1e-9:
        end = start + math.pi
        return _slice(start, end) + " " + _slice(end, start + 2 * math.pi - 1e-6)
    large = 1 if end - start > math.pi else 0
    ox1, oy1 = _point(centre, PIE_OUTER, start)
    ox2, oy2 = _point(centre, PIE_OUTER, end)
    ix2, iy2 = _point(centre, PIE_INNER, end)
    ix1, iy1 = _point(centre, PIE_INNER, start)
    return (
        f"M{ox1} {oy1} A{PIE_OUTER} {PIE_OUTER} 0 {large} 1 {ox2} {oy2} "
        f"L{ix2} {iy2} A{PIE_INNER} {PIE_INNER} 0 {large} 0 {ix1} {iy1} Z"
    )


def pie_chart(data: list[Datum]) -> Chart:
    """Return a ring with a slice per item above zero; the legend lists every item.

    Shares are whole percentages that add up to 100 (largest remainder), so the legend never
    says 99 or 101. A pie of only zeros has no slices and no shares.
    """
    total = sum(d.value for d in data)
    shares = _shares([d.value for d in data], total)
    marks = []
    angle = 0.0
    for index, datum in enumerate(data):
        path = ""
        if datum.value > 0 and total > 0:
            sweep = 2 * math.pi * datum.value / total
            path = _slice(angle, angle + sweep)
            angle += sweep
        marks.append(
            Mark(
                series_of(index),
                datum.label,
                datum.label,
                str(datum.value),
                path=path,
                share=shares[index],
            )
        )
    return Chart(
        ChartKind.PIE, "", PIE_SIZE, PIE_SIZE, tuple(marks), total=total, total_text=str(total)
    )


def _shares(values: list[int], total: int) -> list[int]:
    """Return whole percentages of ``values`` that sum to 100 (largest remainder), or zeros."""
    if total <= 0:
        return [0] * len(values)
    exact = [100 * v / total for v in values]
    floors = [int(e) for e in exact]
    leftover = 100 - sum(floors)
    order = sorted(range(len(values)), key=lambda i: exact[i] - floors[i], reverse=True)
    for i in order[:leftover]:
        floors[i] += 1
    return floors


def build(kind: ChartKind, data: list[Datum]) -> Chart:
    """Return the chart of ``kind`` for ``data``."""
    if kind is ChartKind.PIE:
        return pie_chart(data)
    if kind is ChartKind.LINE:
        return line_chart(data)
    return bar_chart(data)


# --- Report charts (ADR 0117 point 10, step 6) --------------------------------------------------
#
# Columns, stacked columns and lines over categories (days, duration buckets, purposes), for the
# usage reports. Rules the geometry keeps, each tested:
#
# * A chart is never the only carrier of its numbers: the template puts a table made from the very
#   same ``ReportChart`` (``ReportChart.rows``) right after it.
# * A series differs by pattern (column) or by dash and marker shape (line) as well as by colour.
# * A hidden cell (``None``: a small count that ``usage_suppress`` took away) is a gap. Nothing
#   in the geometry depends on its value: it is a marker of one fixed height with the label the
#   tables use (``< 5``), and the scale is made from the shown values only (abuse case R2).

REPORT_WIDTH = 640
"""The report drawing's width in user units; CSS scales it, and the frame scrolls when narrow."""
REPORT_HEIGHT = 320
REPORT_LEFT = 56
REPORT_RIGHT = 16
REPORT_TOP = 52
REPORT_BOTTOM = 72
GAP_HEIGHT = 10
"""The height of the marker for a hidden cell: fixed, so it says nothing about the value."""
MAX_REPORT_SERIES = 4
"""Colours, patterns and markers exist for this many series."""
MAX_CATEGORY_LABELS = 12
"""More categories than this show every n-th label (the table has them all)."""
SCALE_STEPS = (1, 2, 5)
LEGEND_CHAR_WIDTH = 7
"""A legend text's estimated width per character, to space the entries."""


class ReportKind(StrEnum):
    """The kinds of report chart there are."""

    COLUMN = "column"
    """One series as columns."""
    STACKED = "stacked"
    """Several series stacked in one column per category."""
    LINE = "line"
    """Series as lines over ordered categories, broken at a hidden cell."""


@dataclass(frozen=True)
class ReportSeries:
    """One series of a report chart.

    Attributes:
        label: The words for the series (legend, table head); from the catalogue, not free text.
        values: One value per category; ``None`` is a hidden cell and becomes a gap.
    """

    label: str
    values: tuple[int | None, ...]


@dataclass(frozen=True)
class ReportChart:
    """Everything one report chart and its table show.

    Attributes:
        chart_id: A unique id on the page (lowercase letters, digits and hyphens); prefixes the
            ids inside the drawing.
        kind: The kind of chart.
        title: The accessible name and the caption's text.
        description: What the chart shows, in a sentence, for the drawing's description.
        categories: The labels along the horizontal axis, in order.
        series: One to ``MAX_REPORT_SERIES`` series, each with a value per category.
        unit: The unit of the vertical axis, as text (``%``, ``bookings``).
        hidden_label: What a hidden cell is called, such as ``< 5``; the tables use the same.
        gap_note: The legend's words for the gap marker, such as ``Count hidden (< 5)``.
        y_max: A fixed top of the scale (100 for a percentage); ``None`` derives it from the
            shown values.
        total: The total for the table's last row (``None`` when hidden).
        total_label: The words for the total row; empty for no total row.
        drill: A link per category to the same page with a narrower filter (a day), or ``None``
            for a category that has none; empty for a chart without drill-down.
    """

    chart_id: str
    kind: ReportKind
    title: str
    description: str
    categories: tuple[str, ...]
    series: tuple[ReportSeries, ...]
    unit: str
    hidden_label: str
    gap_note: str
    y_max: int | None = None
    total: int | None = None
    total_label: str = ""
    drill: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a chart that cannot be drawn truthfully.

        Raises:
            ValueError: If the id is not an id, there are no or too many series, a series does
                not have a value per category, a value is negative, or a column chart has more
                than one series.
        """
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
        if not (
            0 < len(self.chart_id) <= 40
            and self.chart_id[0].isalpha()
            and set(self.chart_id) <= allowed
        ):
            raise ValueError("chart_id is not an id")
        if not 1 <= len(self.series) <= MAX_REPORT_SERIES:
            raise ValueError(f"a chart needs one to {MAX_REPORT_SERIES} series")
        if self.drill and len(self.drill) != len(self.categories):
            raise ValueError("a drill-down link is needed per category, or none")
        if self.kind is ReportKind.COLUMN and len(self.series) != 1:
            raise ValueError("a column chart has one series")
        for series in self.series:
            if len(series.values) != len(self.categories):
                raise ValueError("a series needs a value per category")
            if any(v is not None and v < 0 for v in series.values):
                raise ValueError("a value is negative")

    @property
    def has_values(self) -> bool:
        """Return whether any cell is shown; a chart of gaps only is left to its table."""
        return any(v is not None for s in self.series for v in s.values)

    def rows(self) -> list[tuple[str, tuple[int | None, ...]]]:
        """Return the table beside the chart: a category and a value per series (hidden: None)."""
        return [
            (name, tuple(s.values[i] for s in self.series))
            for i, name in enumerate(self.categories)
        ]


@dataclass(frozen=True)
class Label:
    """A text in the drawing.

    Attributes:
        text: The words.
        x: The anchor's horizontal position.
        y: The baseline.
        anchor: The SVG ``text-anchor``.
        rotate: Degrees to turn the text about its anchor (crowded category labels), or 0.
    """

    text: str
    x: float
    y: float
    anchor: str = "middle"
    rotate: int = 0


@dataclass(frozen=True)
class Column:
    """A column, or one segment of a stacked column.

    Attributes:
        series: The series index (0 is solid, the others are patterned).
        x: Left edge.
        y: Top edge.
        width: Width.
        height: Height, proportional to the shown value.
        title: The hover text: category, series and value.
        category: The index of the category (the table row beside the chart).
    """

    series: int
    x: float
    y: float
    width: float
    height: float
    title: str
    category: int = 0


@dataclass(frozen=True)
class Gap:
    """The marker of a hidden cell: a fixed size, so it says nothing about the value."""

    x: float
    y: float
    width: float
    height: float
    title: str
    category: int = 0


@dataclass(frozen=True)
class Run:
    """A stretch of a line without a gap: at least two points."""

    series: int
    points: str


@dataclass(frozen=True)
class Marker:
    """The marker of one shown point of a line.

    Attributes:
        series: The series index, which decides the shape.
        shape: ``circle``, ``square``, ``triangle`` or ``diamond``.
        x: Centre.
        y: Centre.
        points: The polygon's points (triangle and diamond), empty for the others.
        title: The hover text.
        category: The index of the category (the table row beside the chart).
    """

    series: int
    shape: str
    x: float
    y: float
    points: str
    title: str
    category: int = 0


@dataclass(frozen=True)
class GridLine:
    """A horizontal grid line at a scale step, with the scale's number."""

    y: float
    label: Label


@dataclass(frozen=True)
class LegendItem:
    """One legend entry: a swatch at ``x`` and its words."""

    series: int
    label: Label
    x: float


@dataclass(frozen=True)
class ReportGeometry:
    """A report chart as geometry, for the template ``admin/_report_chart.html``.

    Attributes:
        width: The ``viewBox`` width.
        height: The ``viewBox`` height.
        plot_left: Left edge of the plot area.
        plot_right: Right edge.
        plot_bottom: The baseline.
        unit: The unit's label above the scale.
        grid: The grid lines with the scale's numbers.
        categories: The category labels that are drawn.
        columns: The columns (column and stacked kinds).
        runs: The line stretches (line kind).
        markers: The line markers.
        gaps: The markers of hidden cells.
        legend: The legend entries.
        gap_legend: The legend entry of the gap marker, or ``None`` when no cell is hidden.
        gap_legend_x: Where that entry's swatch sits.
        patterns: How many series need a pattern fill (columns only), the first series is solid.
    """

    width: int
    height: int
    plot_left: int
    plot_right: int
    plot_bottom: int
    unit: Label
    grid: tuple[GridLine, ...]
    categories: tuple[Label, ...]
    columns: tuple[Column, ...]
    runs: tuple[Run, ...]
    markers: tuple[Marker, ...]
    gaps: tuple[Gap, ...]
    legend: tuple[LegendItem, ...]
    gap_legend: Label | None
    gap_legend_x: float
    patterns: tuple[int, ...]


def nice_max(value: int) -> tuple[int, int]:
    """Return ``(top, step)`` of a scale that covers ``value`` in at most five steps.

    The step is 1, 2 or 5 times a power of ten; the top is a multiple of it. A ``value`` of 1 or
    less gives a scale of 0 to 1.
    """
    if value <= 1:
        return 1, 1
    if value <= 5:
        return value, 1
    magnitude = 10 ** max(0, math.floor(math.log10(value)) - 1)
    for scale in (magnitude, magnitude * 10):
        for factor in SCALE_STEPS:
            step = scale * factor
            if value / step <= 5:
                return math.ceil(value / step) * step, step
    return value, value  # pragma: no cover  (a step of ten times the magnitude always fits)


def _round(value: float) -> float:
    """Return ``value`` to one decimal: stable coordinates."""
    return round(value, 1)


def _scale_of(chart: ReportChart) -> tuple[int, int]:
    """Return the top and the step of the scale, from the shown values only."""
    if chart.y_max is not None:
        return chart.y_max, max(1, chart.y_max // 4)
    if chart.kind is ReportKind.LINE:
        shown = [v for s in chart.series for v in s.values if v is not None]
    else:
        shown = [
            sum(v for s in chart.series if (v := s.values[i]) is not None)
            for i in range(len(chart.categories))
        ]
    return nice_max(max(shown, default=0))


def _marker_of(index: int, x: float, y: float, title: str, category: int = 0) -> Marker:
    """Return the marker of series ``index`` at a point: circle, square, triangle or diamond."""
    x, y = _round(x), _round(y)
    if index == 0:
        return Marker(index, "circle", x, y, "", title, category)
    if index == 1:
        return Marker(index, "square", x, y, "", title, category)
    if index == 2:
        points = (
            f"{x},{_round(y - 5)} {_round(x + 5)},{_round(y + 4)} {_round(x - 5)},{_round(y + 4)}"
        )
        return Marker(index, "triangle", x, y, points, title, category)
    points = f"{x},{_round(y - 5)} {_round(x + 5)},{y} {x},{_round(y + 5)} {_round(x - 5)},{y}"
    return Marker(index, "diamond", x, y, points, title, category)


def _columns_of(
    chart: ReportChart, top: int, centres: list[float], slot: float
) -> tuple[list[Column], list[Gap]]:
    """Return the columns (stacked when there are several series) and the gap markers."""
    plot_h = REPORT_HEIGHT - REPORT_BOTTOM - REPORT_TOP
    base = REPORT_HEIGHT - REPORT_BOTTOM
    width = min(slot * 0.6, 56)
    columns: list[Column] = []
    gaps: list[Gap] = []
    for i, name in enumerate(chart.categories):
        y = float(base)
        hidden = False
        for j, series in enumerate(chart.series):
            value = series.values[i]
            if value is None:
                hidden = True
            elif value > 0:
                height = plot_h * value / top
                y -= height
                columns.append(
                    Column(
                        j,
                        _round(centres[i] - width / 2),
                        _round(y),
                        _round(width),
                        _round(height),
                        f"{name} – {series.label}: {value}",
                        i,
                    )
                )
        if hidden:
            gaps.append(
                Gap(
                    _round(centres[i] - width / 2),
                    _round(y - GAP_HEIGHT),
                    _round(width),
                    GAP_HEIGHT,
                    f"{name}: {chart.hidden_label}",
                    i,
                )
            )
    return columns, gaps


def _lines_of(
    chart: ReportChart, top: int, centres: list[float]
) -> tuple[list[Run], list[Marker], list[Gap]]:
    """Return the line stretches (broken at a hidden cell), the markers and the gap markers."""
    plot_h = REPORT_HEIGHT - REPORT_BOTTOM - REPORT_TOP
    base = REPORT_HEIGHT - REPORT_BOTTOM
    runs: list[Run] = []
    markers: list[Marker] = []
    gaps: list[Gap] = []
    hidden_at: set[int] = set()
    for j, series in enumerate(chart.series):
        run: list[str] = []
        for i, value in enumerate(series.values):
            if value is None:
                hidden_at.add(i)
                if len(run) > 1:
                    runs.append(Run(j, " ".join(run)))
                run = []
                continue
            y = base - plot_h * value / top
            run.append(f"{_round(centres[i])},{_round(y)}")
            title = f"{chart.categories[i]} – {series.label}: {value}"
            markers.append(_marker_of(j, centres[i], y, title, i))
        if len(run) > 1:
            runs.append(Run(j, " ".join(run)))
    for i in sorted(hidden_at):
        gaps.append(
            Gap(
                _round(centres[i] - 6),
                base - GAP_HEIGHT,
                12,
                GAP_HEIGHT,
                f"{chart.categories[i]}: {chart.hidden_label}",
                i,
            )
        )
    return runs, markers, gaps


def report_geometry(chart: ReportChart) -> ReportGeometry:
    """Return the geometry of ``chart``: the same chart always gives the same numbers.

    The first series of a column chart is solid and the others are patterned; lines differ by
    marker shape. A hidden cell is a marker of ``GAP_HEIGHT`` and the scale is made from the
    shown values only, so nothing here depends on a hidden value.
    """
    top, step = _scale_of(chart)
    n = len(chart.categories)
    slot = (REPORT_WIDTH - REPORT_LEFT - REPORT_RIGHT) / max(n, 1)
    centres = [REPORT_LEFT + slot * (i + 0.5) for i in range(n)]
    base = REPORT_HEIGHT - REPORT_BOTTOM
    plot_h = base - REPORT_TOP
    columns: list[Column] = []
    runs: list[Run] = []
    markers: list[Marker] = []
    if chart.kind is ReportKind.LINE:
        runs, markers, gaps = _lines_of(chart, top, centres)
    else:
        columns, gaps = _columns_of(chart, top, centres, slot)
    grid = tuple(
        GridLine(
            _round(base - plot_h * tick / top),
            Label(str(tick), REPORT_LEFT - 8, _round(base - plot_h * tick / top + 4), "end"),
        )
        for tick in range(0, top + 1, step)
    )
    stride = max(1, math.ceil(n / MAX_CATEGORY_LABELS))
    label_y = base + 16
    categories = tuple(
        Label(
            chart.categories[i],
            _round(centres[i]),
            label_y,
            "end" if n > 6 else "middle",
            -35 if n > 6 else 0,
        )
        for i in range(0, n, stride)
    )
    legend: list[LegendItem] = []
    x = float(REPORT_LEFT)
    for j, series in enumerate(chart.series):
        legend.append(LegendItem(j, Label(series.label, x + 26, 18, "start"), x))
        x += 26 + LEGEND_CHAR_WIDTH * len(series.label) + 18
    return ReportGeometry(
        REPORT_WIDTH,
        REPORT_HEIGHT,
        REPORT_LEFT,
        REPORT_WIDTH - REPORT_RIGHT,
        base,
        Label(chart.unit, REPORT_LEFT, REPORT_TOP - 12, "start"),
        grid,
        categories,
        tuple(columns),
        tuple(runs),
        tuple(markers),
        tuple(gaps),
        tuple(legend),
        Label(chart.gap_note, x + 26, 18, "start") if gaps else None,
        x,
        tuple(range(1, len(chart.series))) if chart.kind is not ReportKind.LINE else (),
    )
