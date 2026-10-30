"""The chart of a widget as its template needs it (ADR 0061 §5, OV-4).

``app.web.charts`` builds the geometry and the text summary from plain data; this module adds
what only the admin overview knows: the translated kind name, the title and unit of a widget's
series, and the rows of the table that sits behind every chart in a ``<details>``. It also
builds the links that switch a widget between the views its register entry supports, as plain
``GET`` links to the overview (no script).

A widget draws a chart only for the views ``bar``, ``line`` and ``pie``; a widget without a
series (``CHART_SERIES``) draws its list or table whatever view is asked for.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

from app.i18n import BoundGettext
from app.web.charts import Chart, ChartKind, Datum, build, describe
from app.web.widgets import OVERVIEW_PATH, VIEW_LABELS, View, Widget, resolve_view

KIND_OF_VIEW: dict[View, ChartKind] = {
    View.BAR: ChartKind.BAR,
    View.LINE: ChartKind.LINE,
    View.PIE: ChartKind.PIE,
}
"""The views that are charts, and the chart each one draws."""

KIND_LABELS: dict[ChartKind, str] = {
    ChartKind.BAR: "admin.overview.chart.bar",
    ChartKind.LINE: "admin.overview.chart.line",
    ChartKind.PIE: "admin.overview.chart.pie",
}
"""Catalogue key of each chart's name (the start of its text summary)."""


@dataclass(frozen=True)
class Series:
    """What a widget's chart counts: the catalogue keys of its unit and of its one value.

    Attributes:
        column_key: The table column header, e.g. "Waiting".
        item_key: The sentence of one value in the summary, with ``{label}`` and ``{value}``.
    """

    column_key: str
    item_key: str


CHART_SERIES: dict[str, Series] = {
    "bookings-today": Series(
        "admin.overview.widget.bookings_today.series",
        "admin.overview.widget.bookings_today.chart_item",
    ),
    "queue": Series(
        "admin.overview.widget.queue.waiting",
        "admin.overview.widget.queue.chart_item",
    ),
}
"""The widgets that can draw a chart, and the one value each one counts per group."""


@dataclass(frozen=True)
class ChartView:
    """One chart, ready for ``admin/widgets/_chart.html``.

    Attributes:
        chart: The geometry.
        kind_label: The translated name of the kind, e.g. "Bar chart".
        summary: The text summary with every value (the SVG's ``aria-label``).
        group_header: The table's first column header.
        value_header: The table's second column header.
        rows: The table rows: the whole label and the printed value.
    """

    chart: Chart
    kind_label: str
    summary: str
    group_header: str
    value_header: str
    rows: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ViewChoice:
    """One link of a widget's view switcher.

    Attributes:
        label: The translated view name.
        href: The overview with this widget set to the view.
        current: Whether the widget shows this view now.
    """

    label: str
    href: str
    current: bool


def chart_view(
    view: View, widget: Widget, data: list[Datum], title: str, _: BoundGettext
) -> ChartView | None:
    """Return the chart for ``view`` of ``widget``'s ``data``, or ``None`` when none is due.

    ``None`` for a list or table view, for a widget without a series, and for no data: the
    widget then shows its own list, table or empty state.
    """
    kind = KIND_OF_VIEW.get(view)
    series = CHART_SERIES.get(widget.key)
    if kind is None or series is None or not data:
        return None
    kind_label = _(KIND_LABELS[kind])
    summary = describe(
        f"{kind_label}, {title}",
        data,
        lambda d: _(series.item_key, label=d.label, value=d.value),
        _("admin.overview.chart.joiner"),
    )
    return ChartView(
        build(kind, data),
        kind_label,
        summary,
        _("admin.overview.chart.group"),
        _(series.column_key),
        tuple((d.label, str(d.value)) for d in data),
    )


def page_href(selected: Mapping[str, View], widgets: Mapping[str, Widget]) -> str:
    """Return the overview address that keeps ``selected`` (widget key to its chosen view).

    A widget at its default view is left out, so the common address stays ``/admin/``.
    """
    chosen = {
        key: view.value
        for key, view in selected.items()
        if key in widgets and view is not widgets[key].views[0]
    }
    return f"{OVERVIEW_PATH}?{urlencode(chosen)}" if chosen else OVERVIEW_PATH


def explicit_views(widgets: Mapping[str, Widget], params: Mapping[str, str]) -> dict[str, View]:
    """Return the views the address asks for: one per widget whose parameter names a valid one.

    The address is input from outside: a widget key that is not shown, a view the widget does
    not support or garbage is ignored, so it can neither draw an unsupported chart nor echo.
    """
    asked: dict[str, View] = {}
    for key, item in widgets.items():
        if key in params and len(item.views) > 1:
            asked[key] = resolve_view(item, params[key])
    return asked


def view_choices(
    item: Widget,
    shown: View,
    selected: Mapping[str, View],
    widgets: Mapping[str, Widget],
    _: BoundGettext,
) -> tuple[ViewChoice, ...]:
    """Return the switcher links of ``item``; none when it supports only one view."""
    if len(item.views) < 2:
        return ()
    return tuple(
        ViewChoice(
            _(VIEW_LABELS[view]),
            page_href({**selected, item.key: view}, widgets),
            view is shown,
        )
        for view in item.views
    )
