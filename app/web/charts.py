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

    ``item`` writes one value as a translated sentence (e.g. "Aangifte: 4 waiting"), ``joiner``
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
