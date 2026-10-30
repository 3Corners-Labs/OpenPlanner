"""The geometry and the text of the overview charts (ADR 0061 §5): pure, no markup."""

import ast
import re
from pathlib import Path

import pytest

from app.web import charts
from app.web.charts import (
    BAR_HEIGHT,
    BAR_WIDTH,
    LABEL_LIMIT,
    PIE_SIZE,
    SERIES_CLASSES,
    ChartKind,
    Datum,
    bar_chart,
    build,
    describe,
    line_chart,
    pie_chart,
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
