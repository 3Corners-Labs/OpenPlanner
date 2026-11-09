"""The charts of the usage reports, from the domain reports and the words (ADR 0117 step 6).

``app.web.charts`` draws; this module decides what is drawn. A function per screen turns the
report (counts with hidden cells already ``None``) into ``ChartView`` objects: the chart model
with its words from the catalogue, the geometry, and the header of its table. The template
``admin/_report_chart.html`` draws a chart and, directly after it, a table made from the same
model, so the numbers never differ and are never only in the picture.

A hidden count stays ``None`` all the way: the gap marker and the table's ``< n`` come from the
suppression of the domain, and nothing here looks at, adds up or rounds a hidden value.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.domain.usage_report import CountTable, VolumeReport
from app.domain.usage_report_capacity import CapacityReport
from app.domain.usage_report_channels import ChannelsReport
from app.domain.usage_report_queue import QueueReport
from app.i18n import BoundGettext
from app.web.charts import (
    ReportChart,
    ReportGeometry,
    ReportKind,
    ReportSeries,
    report_geometry,
)
from app.web.usage_labels import label

DESCRIPTIONS: dict[ReportKind, str] = {
    ReportKind.COLUMN: "reports.chart.desc.column",
    ReportKind.STACKED: "reports.chart.desc.stacked",
    ReportKind.LINE: "reports.chart.desc.line",
}
"""The sentence that describes each kind of chart; the catalogue test sees every key used."""

WEEKEND_FROM = 5
"""``date.weekday()`` of the first weekend day (Saturday)."""

Labeller = Callable[[str, str], str]
"""``(table, code) -> words``: ``usage_labels.label`` with the page's translation bound."""


@dataclass(frozen=True)
class ChartView:
    """One chart as its template needs it.

    Attributes:
        chart: The model: words, categories, series and values.
        geometry: The drawing's numbers.
        head: The header of the table's first column.
    """

    chart: ReportChart
    geometry: ReportGeometry
    head: str


def _view(
    _: BoundGettext,
    chart_id: str,
    kind: ReportKind,
    title: str,
    head: str,
    categories: Sequence[str],
    series: Sequence[ReportSeries],
    *,
    unit: str,
    threshold: int,
    total: int | None = None,
    total_label: str = "",
    y_max: int | None = None,
) -> ChartView:
    """Return the chart view with the sentences every chart carries."""
    hidden = _("reports.hidden", n=threshold)
    chart = ReportChart(
        chart_id=chart_id,
        kind=kind,
        title=title,
        description=_(DESCRIPTIONS[kind], title=title, hidden=hidden),
        categories=tuple(categories),
        series=tuple(series),
        unit=unit,
        hidden_label=hidden,
        gap_note=_("reports.chart.gap", hidden=hidden),
        y_max=y_max,
        total=total,
        total_label=total_label,
    )
    return ChartView(chart, report_geometry(chart), head)


def _columns_of(
    _: BoundGettext,
    chart_id: str,
    title: str,
    head: str,
    table: CountTable,
    words: Labeller,
    kind_table: str,
    unit: str,
    threshold: int,
) -> ChartView:
    """Return the column chart of a count table, with its total row."""
    return _view(
        _,
        chart_id,
        ReportKind.COLUMN,
        title,
        head,
        [words(kind_table, r.code) for r in table.rows],
        [ReportSeries(unit, tuple(r.count for r in table.rows))],
        unit=unit,
        threshold=threshold,
        total=table.total,
        total_label=_("reports.total"),
    )


def queue_charts(report: QueueReport, _: BoundGettext) -> dict[str, ChartView]:
    """Return the charts of the *Queue and calls* screen: the waits, the length and the peak."""
    words: Labeller = lambda table, code: label(_, table, code)  # noqa: E731
    calls = _("reports.chart.unit.calls")
    charts: dict[str, ChartView] = {}
    for name, key, head, dist in (
        (
            "task_wait",
            "reports.queue.task_wait.title",
            "reports.queue.task_wait.bucket",
            report.task_wait,
        ),
        (
            "call_length",
            "reports.queue.length.title",
            "reports.queue.length.bucket",
            report.call_length,
        ),
    ):
        charts[name] = _columns_of(
            _,
            f"chart-{name.replace('_', '-')}",
            _(key),
            _(head),
            dist.table,
            words,
            "buckets",
            calls,
            report.threshold,
        )
    days = [p.day.isoformat() for p in report.peaks]
    charts["peak"] = _view(
        _,
        "chart-peak",
        ReportKind.LINE,
        _("reports.queue.peak.title"),
        _("reports.created.day"),
        days,
        [ReportSeries(_("reports.queue.peak.series"), tuple(p.peak for p in report.peaks))],
        unit=_("reports.chart.unit.calls"),
        threshold=report.threshold,
    )
    return charts


def volume_charts(report: VolumeReport, _: BoundGettext) -> dict[str, ChartView]:
    """Return the charts of the *Volume* screen: bookings per day, and how they ended.

    The day chart has two series, weekday and weekend, so that a weekend is told apart by its
    pattern and not by its colour alone; a day holds a value in exactly one of them. The outcome
    bar is one stacked column in four series; the table after it keeps the finer outcomes.
    """
    words: Labeller = lambda table, code: label(_, table, code)  # noqa: E731
    weekday: list[int | None] = []
    weekend: list[int | None] = []
    for d in report.days:
        hidden = d.count is None
        weekend_day = d.day.weekday() >= WEEKEND_FROM
        weekday.append(None if hidden else (0 if weekend_day else d.count))
        weekend.append(None if hidden else (d.count if weekend_day else 0))
    by_outcome = {r.code: r.count for r in report.outcomes.rows}
    cancelled_codes = [c for c in by_outcome if c.startswith("cancelled_")]
    cancelled_parts = [by_outcome[c] for c in cancelled_codes]
    cancelled = None if None in cancelled_parts else sum(p or 0 for p in cancelled_parts)
    outcome_series = [
        ReportSeries(words("outcomes", "completed"), (by_outcome.get("completed"),)),
        ReportSeries(words("outcomes", "no_show"), (by_outcome.get("no_show"),)),
        ReportSeries(_("reports.volume.outcome.cancelled"), (cancelled,)),
        ReportSeries(
            words("outcomes", "expired_unconfirmed"), (by_outcome.get("expired_unconfirmed"),)
        ),
    ]
    unit = _("reports.chart.unit.bookings")
    return {
        "days": _view(
            _,
            "chart-days",
            ReportKind.STACKED,
            _("reports.volume.days.title"),
            _("reports.created.day"),
            [d.day.isoformat() for d in report.days],
            [
                ReportSeries(_("reports.volume.days.weekday"), tuple(weekday)),
                ReportSeries(_("reports.volume.days.weekend"), tuple(weekend)),
            ],
            unit=unit,
            threshold=report.threshold,
            total=report.created_total,
            total_label=_("reports.total"),
        ),
        "outcome": _view(
            _,
            "chart-outcome",
            ReportKind.STACKED,
            _("reports.volume.outcome.title"),
            _("reports.outcomes.outcome"),
            [_("reports.volume.outcome.all")],
            outcome_series,
            unit=unit,
            threshold=report.threshold,
        ),
    }


def capacity_charts(report: CapacityReport, _: BoundGettext) -> dict[str, ChartView]:
    """Return the charts of the *Capacity* screen: utilisation per day and the fill of slots."""
    words: Labeller = lambda table, code: label(_, table, code)  # noqa: E731
    charts = {
        "fill": _columns_of(
            _,
            "chart-fill",
            _("reports.capacity.fill.title"),
            _("reports.capacity.fill.class"),
            report.fill,
            words,
            "fill",
            _("reports.chart.unit.slots"),
            report.threshold,
        ),
        "utilisation": _view(
            _,
            "chart-utilisation",
            ReportKind.LINE,
            _("reports.capacity.utilisation.title"),
            _("reports.created.day"),
            [d.day.isoformat() for d in report.days],
            [
                ReportSeries(
                    _("reports.capacity.utilisation.series"),
                    tuple(d.utilisation_percent for d in report.days),
                )
            ],
            unit=_("reports.chart.unit.percent"),
            threshold=report.threshold,
            y_max=100,
        ),
    }
    return charts


def channels_charts(report: ChannelsReport, _: BoundGettext) -> dict[str, ChartView]:
    """Return the charts of the *Channels* screen: messages per purpose, and the trial funnel."""
    words: Labeller = lambda table, code: label(_, table, code)  # noqa: E731
    messages = report.messages
    sent = [row[0] for row in messages.by_purpose.cells]
    failed = [row[1] for row in messages.by_purpose.cells]
    return {
        "messages": _view(
            _,
            "chart-messages",
            ReportKind.STACKED,
            _("reports.channels.messages.title"),
            _("reports.channels.purpose"),
            [words("purposes", p) for p in messages.by_purpose.rows],
            [
                ReportSeries(_("reports.channels.sent"), tuple(sent)),
                ReportSeries(_("reports.channels.failed"), tuple(failed)),
            ],
            unit=_("reports.chart.unit.messages"),
            threshold=report.threshold,
        ),
        "trial": _columns_of(
            _,
            "chart-trial",
            _("reports.channels.trial.title"),
            _("reports.channels.trial.step"),
            report.trial,
            words,
            "trial",
            _("reports.chart.unit.trials"),
            report.threshold,
        ),
    }
