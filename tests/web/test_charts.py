"""The geometry and the text of the overview charts (ADR 0061 §5): pure, no markup."""

import ast
import re
from pathlib import Path

import pytest

from app.web import charts
from app.web.charts import (
    BAR_HEIGHT,
    BAR_WIDTH,
    GAP_HEIGHT,
    LABEL_LIMIT,
    PIE_SIZE,
    SERIES_CLASSES,
    ChartKind,
    Datum,
    ReportChart,
    ReportKind,
    ReportSeries,
    bar_chart,
    build,
    describe,
    line_chart,
    nice_max,
    pie_chart,
    report_geometry,
    series_of,
    shorten,
)

THREE = [Datum("Aangifte", 4), Datum("Bezwaar", 2), Datum("Toeslagen", 0)]
SIX = [Datum(f"Groep {n}", n) for n in range(1, 7)]
PATH_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def test_a_label_is_shortened_with_an_ellipsis_only_when_too_long() -> None:
    assert shorten("Aangifte") == "Aangifte"
    assert shorten("x" * LABEL_LIMIT) == "x" * LABEL_LIMIT
    assert shorten("x" * (LABEL_LIMIT + 1)) == "x" * (LABEL_LIMIT - 1) + "…"
    assert shorten("Demo: videogesprek", 10) == "Demo: vid…"
    assert shorten("Demo:  groot", 6) == "Demo:…"  # no dangling space before the ellipsis


def test_the_palette_repeats_after_the_defined_series() -> None:
    assert [series_of(i) for i in range(SERIES_CLASSES + 2)] == [0, 1, 2, 3, 4, 5, 0, 1]


def test_the_summary_names_the_title_and_every_value() -> None:
    def sentence(d: Datum) -> str:
        return f"{d.label}: {d.value}"

    summary = describe("Afspraken vandaag", THREE, sentence, "; ")

    assert summary == "Afspraken vandaag: Aangifte: 4; Bezwaar: 2; Toeslagen: 0"
    assert describe("Leeg", [], sentence, "; ") == "Leeg: "


def test_up_to_four_items_stand_upright_and_more_lie_down() -> None:
    assert bar_chart(THREE).orientation == "vertical"
    assert bar_chart(SIX[:4]).orientation == "vertical"
    assert bar_chart(SIX[:5]).orientation == "horizontal"
    assert bar_chart(SIX).orientation == "horizontal"


def test_vertical_bars_are_proportional_and_carry_their_value_as_text() -> None:
    chart = bar_chart(THREE)
    tall, half, none = chart.marks

    assert (chart.kind, chart.width, chart.height) == (ChartKind.BAR, BAR_WIDTH, BAR_HEIGHT)
    assert half.height == pytest.approx(tall.height / 2, abs=0.1)
    assert none.height == 0
    assert [m.value_text for m in chart.marks] == ["4", "2", "0"]  # a zero bar still says so
    assert [m.full_label for m in chart.marks] == ["Aangifte", "Bezwaar", "Toeslagen"]
    assert tall.y + tall.height == pytest.approx(chart.axis[1])  # bars stand on the axis
    assert tall.value_y < tall.y  # the value sits above its bar
    assert tall.x < half.x < none.x
    assert [m.series for m in chart.marks] == [0, 1, 2]


def test_horizontal_bars_grow_to_the_right_with_the_value_at_their_end() -> None:
    chart = bar_chart(SIX)
    first, *_, last = chart.marks

    assert chart.height == 6 * charts.ROW_HEIGHT + 8
    assert first.width == pytest.approx(last.width / 6, abs=0.1)
    assert last.x + last.width + 6 == pytest.approx(last.value_x, abs=0.1)
    assert last.value_anchor == "start"
    assert first.label_anchor == "end"
    assert [m.y for m in chart.marks] == sorted(m.y for m in chart.marks)
    assert len({m.y for m in chart.marks}) == 6  # one row each
    assert chart.axis == (charts.LABEL_AREA, 0, charts.LABEL_AREA, chart.height)


def test_long_labels_are_shortened_in_the_picture_and_whole_in_the_data() -> None:
    chart = bar_chart([Datum("Een heel lange groepsnaam", 1)] * 5)

    assert chart.marks[0].label.endswith("…")
    assert len(chart.marks[0].label) <= LABEL_LIMIT
    assert chart.marks[0].full_label == "Een heel lange groepsnaam"


def test_a_chart_of_only_zeros_or_nothing_does_not_divide_by_zero() -> None:
    zeros = [Datum("A", 0), Datum("B", 0)]

    assert [m.height for m in bar_chart(zeros).marks] == [0, 0]
    assert [m.width for m in bar_chart(zeros * 3).marks] == [0] * 6
    assert bar_chart([]).marks == ()
    assert line_chart([]).marks == ()
    assert line_chart([]).points == ""
    assert pie_chart([]).marks == ()


def test_the_line_runs_left_to_right_through_a_marker_per_item() -> None:
    chart = line_chart(THREE)
    first, second, third = chart.marks

    assert chart.kind is ChartKind.LINE
    assert first.x < second.x < third.x
    assert first.y < second.y < third.y  # a higher value is higher up (smaller y)
    assert chart.points == " ".join(f"{m.x},{m.y}" for m in chart.marks)
    assert [m.value_text for m in chart.marks] == ["4", "2", "0"]
    assert third.y == pytest.approx(chart.axis[1])  # zero sits on the axis
    assert all(m.value_y < m.y for m in chart.marks)


def test_a_line_of_one_item_is_a_single_centred_marker() -> None:
    chart = line_chart([Datum("A", 3)])

    assert len(chart.marks) == 1
    assert chart.marks[0].x == pytest.approx((charts.LINE_LEFT + BAR_WIDTH - charts.LINE_RIGHT) / 2)
    assert chart.points == f"{chart.marks[0].x},{chart.marks[0].y}"


def test_pie_shares_are_whole_percentages_that_add_up_to_one_hundred() -> None:
    thirds = pie_chart([Datum(c, 1) for c in "ABC"])
    uneven = pie_chart([Datum("A", 1), Datum("B", 2), Datum("C", 4), Datum("D", 0)])

    assert sorted(m.share for m in thirds.marks) == [33, 33, 34]
    assert sum(m.share for m in uneven.marks) == 100
    assert [m.share for m in uneven.marks] == [14, 29, 57, 0]
    assert thirds.total == 3
    assert thirds.total_text == "3"


def test_a_pie_draws_a_slice_only_for_values_above_zero_but_lists_every_item() -> None:
    chart = pie_chart(THREE)

    assert chart.kind is ChartKind.PIE
    assert (chart.width, chart.height) == (PIE_SIZE, PIE_SIZE)
    assert [bool(m.path) for m in chart.marks] == [True, True, False]
    assert [m.full_label for m in chart.marks] == ["Aangifte", "Bezwaar", "Toeslagen"]
    assert [m.value_text for m in chart.marks] == ["4", "2", "0"]


def test_pie_slices_stay_inside_the_canvas_and_close() -> None:
    for chart in (pie_chart(THREE), pie_chart([Datum("Alles", 5)]), pie_chart(SIX)):
        for mark in chart.marks:
            if not mark.path:
                continue
            assert mark.path.startswith("M")
            assert mark.path.rstrip().endswith("Z")
            for number in map(float, PATH_NUMBER.findall(mark.path)):
                assert -1 <= number <= PIE_SIZE + 1, mark.path


def test_a_slice_over_half_the_ring_uses_the_large_arc_flag() -> None:
    big, small = pie_chart([Datum("A", 3), Datum("B", 1)]).marks

    assert " 0 1 1 " in big.path  # large-arc-flag 1 on the outer arc
    assert " 0 0 1 " in small.path


def test_a_single_item_fills_the_whole_ring() -> None:
    [only] = pie_chart([Datum("Alles", 5)]).marks

    assert only.share == 100
    assert only.path.count("M") == 2  # two halves: one arc cannot close on itself
    assert only.path.rstrip().endswith("Z")


def test_a_pie_of_only_zeros_has_no_slices_and_no_shares() -> None:
    chart = pie_chart([Datum("A", 0), Datum("B", 0)])

    assert [m.path for m in chart.marks] == ["", ""]
    assert [m.share for m in chart.marks] == [0, 0]
    assert chart.total == 0


def test_build_picks_the_chart_of_the_kind() -> None:
    assert build(ChartKind.BAR, THREE).kind is ChartKind.BAR
    assert build(ChartKind.LINE, THREE).kind is ChartKind.LINE
    assert build(ChartKind.PIE, THREE).kind is ChartKind.PIE


def test_every_mark_stays_inside_its_canvas() -> None:
    for chart in (bar_chart(THREE), bar_chart(SIX), line_chart(THREE), line_chart(SIX)):
        for mark in chart.marks:
            assert 0 <= mark.x <= chart.width, (chart.kind, mark)
            assert 0 <= mark.y <= chart.height, (chart.kind, mark)
            assert mark.value_y >= 0, (chart.kind, mark)
            assert mark.label_y <= chart.height, (chart.kind, mark)


def test_the_module_imports_no_framework() -> None:
    tree = ast.parse(Path(charts.__file__).read_text())
    imported = {
        node.module.split(".")[0]
        if isinstance(node, ast.ImportFrom) and node.module
        else alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }

    assert imported <= {"math", "collections", "dataclasses", "enum"}, imported


# --- Report charts (ADR 0117 point 10) -----------------------------------------------------------


def _report_chart(
    values: tuple[int | None, ...] = (3, 10, 6),
    *,
    kind: ReportKind = ReportKind.COLUMN,
    more: tuple[ReportSeries, ...] = (),
    categories: tuple[str, ...] = ("a", "b", "c"),
    y_max: int | None = None,
) -> ReportChart:
    return ReportChart(
        chart_id="demo",
        kind=kind,
        title="Bookings per day",
        description="Bookings in the range.",
        categories=categories,
        series=(ReportSeries("Bookings", values), *more),
        unit="bookings",
        hidden_label="< 5",
        gap_note="Count hidden (< 5)",
        y_max=y_max,
    )


def test_a_column_is_as_high_as_its_value_on_a_scale_that_fits_the_largest() -> None:
    columns = report_geometry(_report_chart((3, 10, 6))).columns

    assert columns[1].height == pytest.approx(columns[0].height * 10 / 3, rel=0.01)
    assert columns[2].height == pytest.approx(columns[0].height * 2, rel=0.01)


def test_the_same_chart_always_gives_the_same_geometry() -> None:
    assert report_geometry(_report_chart()) == report_geometry(_report_chart())


def test_a_hidden_cell_is_a_marker_of_a_fixed_height_with_its_label() -> None:
    geometry = report_geometry(_report_chart((None, 10, 6)))

    [gap] = geometry.gaps
    assert gap.height == GAP_HEIGHT
    assert gap.title == "a: < 5"
    assert geometry.gap_legend is not None
    assert len(geometry.columns) == 2


def test_nothing_in_the_geometry_depends_on_a_hidden_value() -> None:
    """The model has no number for a hidden cell, so no scale or height can carry one (R2)."""
    chart = _report_chart((None, 10, 6))

    assert report_geometry(chart) == report_geometry(_report_chart((None, 10, 6)))
    # The scale is the one of the shown values: 10 fits in 0 to 10, whatever was hidden.
    assert [g.label.text for g in report_geometry(chart).grid][-1] == "10"


def test_a_gap_in_a_stack_sits_on_the_shown_part_at_the_same_fixed_height() -> None:
    chart = _report_chart(
        (4, 4, 4), kind=ReportKind.STACKED, more=(ReportSeries("Failed", (None, 2, 2)),)
    )

    geometry = report_geometry(chart)

    [gap] = geometry.gaps
    assert gap.height == GAP_HEIGHT
    first = geometry.columns[0]
    assert gap.y == pytest.approx(first.y - GAP_HEIGHT, abs=0.1)


def test_an_all_hidden_chart_has_no_values_and_draws_no_column() -> None:
    chart = _report_chart((None, None, None))

    assert not chart.has_values
    assert report_geometry(chart).columns == ()


def test_zero_values_draw_no_column() -> None:
    assert report_geometry(_report_chart((0, 0, 0))).columns == ()


def test_stacked_series_add_up_and_the_later_ones_are_patterned() -> None:
    chart = _report_chart(
        (4, 5, 6),
        kind=ReportKind.STACKED,
        more=(ReportSeries("Failed", (4, 5, 6)), ReportSeries("Third", (1, 1, 1))),
    )

    geometry = report_geometry(chart)

    assert geometry.patterns == (1, 2)
    first_stack = [c for c in geometry.columns if c.x == geometry.columns[0].x]
    assert [c.series for c in first_stack] == [0, 1, 2]
    assert first_stack[0].y == pytest.approx(first_stack[1].y + first_stack[1].height, abs=0.2)


def test_lines_break_at_a_hidden_cell_and_each_series_has_its_marker_shape() -> None:
    chart = _report_chart(
        (5, None, 7, 9),
        kind=ReportKind.LINE,
        more=(ReportSeries("Second", (1, 2, 3, 4)),),
        categories=("a", "b", "c", "d"),
    )

    geometry = report_geometry(chart)

    assert [(r.series, r.points.count(",")) for r in geometry.runs] == [(0, 2), (1, 4)]
    assert {m.shape for m in geometry.markers if m.series == 0} == {"circle"}
    assert {m.shape for m in geometry.markers if m.series == 1} == {"square"}
    assert [g.title for g in geometry.gaps] == ["b: < 5"]
    assert geometry.patterns == ()


def test_a_fixed_top_of_the_scale_is_kept() -> None:
    geometry = report_geometry(_report_chart((10, 20, 30), kind=ReportKind.LINE, y_max=100))

    assert [g.label.text for g in geometry.grid][-1] == "100"


def test_many_categories_show_every_nth_label_turned() -> None:
    names = tuple(f"d{i}" for i in range(28))

    labels = report_geometry(_report_chart(tuple(range(28)), categories=names)).categories

    assert 0 < len(labels) <= 12
    assert labels[0].text == "d0"
    assert labels[0].rotate == -35


def test_few_categories_all_show_flat() -> None:
    labels = report_geometry(_report_chart()).categories

    assert [label.text for label in labels] == ["a", "b", "c"]
    assert {label.rotate for label in labels} == {0}


def test_rows_give_the_table_beside_the_chart() -> None:
    chart = _report_chart(
        (3, None, 6), more=(ReportSeries("Failed", (1, 2, None)),), kind=ReportKind.STACKED
    )

    assert chart.rows() == [("a", (3, 1)), ("b", (None, 2)), ("c", (6, None))]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, (1, 1)),
        (1, (1, 1)),
        (3, (3, 1)),
        (10, (10, 2)),
        (11, (15, 5)),
        (47, (50, 10)),
        (6, (6, 2)),
    ],
)
def test_nice_max(value: int, expected: tuple[int, int]) -> None:
    assert nice_max(value) == expected


def test_a_chart_that_cannot_be_drawn_truthfully_is_refused() -> None:
    def make(**kw: object) -> ReportChart:
        base: dict[str, object] = {
            "chart_id": "ok",
            "kind": ReportKind.COLUMN,
            "title": "t",
            "description": "d",
            "categories": ("a",),
            "series": (ReportSeries("s", (1,)),),
            "unit": "u",
            "hidden_label": "<",
            "gap_note": "n",
        }
        return ReportChart(**{**base, **kw})  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="id"):
        make(chart_id="Bad Id")
    with pytest.raises(ValueError, match="series"):
        make(series=())
    with pytest.raises(ValueError, match="one series"):
        make(series=(ReportSeries("s", (1,)), ReportSeries("t", (1,))))
    with pytest.raises(ValueError, match="per category"):
        make(categories=("a", "b"))
    with pytest.raises(ValueError, match="negative"):
        make(series=(ReportSeries("s", (-1,)),))


def test_four_lines_have_four_marker_shapes_and_the_legend_has_one_entry_each() -> None:
    chart = _report_chart(
        (1, 2, 3),
        kind=ReportKind.LINE,
        more=(
            ReportSeries("B", (2, 3, 4)),
            ReportSeries("C", (3, 4, 5)),
            ReportSeries("D", (4, 5, 6)),
        ),
    )

    geometry = report_geometry(chart)

    assert {m.series: m.shape for m in geometry.markers} == {
        0: "circle",
        1: "square",
        2: "triangle",
        3: "diamond",
    }
    assert [item.label.text for item in geometry.legend] == ["Bookings", "B", "C", "D"]
    assert all(m.points for m in geometry.markers if m.shape in ("triangle", "diamond"))


def test_a_run_of_one_point_is_a_marker_without_a_line() -> None:
    geometry = report_geometry(_report_chart((None, 5, None), kind=ReportKind.LINE))

    assert geometry.runs == ()
    assert len(geometry.markers) == 1
    assert len(geometry.gaps) == 2
