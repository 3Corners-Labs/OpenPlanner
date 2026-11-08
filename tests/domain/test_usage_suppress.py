"""Small cells are never shown or recovered from totals (ADR 0117 point 8, abuse case R2)."""

import random
from datetime import UTC, datetime

import pytest

from app.domain.usage import UsageCell
from app.domain.usage_suppress import (
    MIN_CELL_SIZE_FLOOR,
    effective_threshold,
    show_cell,
    suppress_line,
    suppress_table,
)

THRESHOLD = 5


def test_the_floor_is_three_and_cannot_be_switched_off() -> None:
    assert MIN_CELL_SIZE_FLOOR == 3
    with pytest.raises(ValueError, match="threshold"):
        suppress_line([10, 10], 2)
    with pytest.raises(ValueError, match="threshold"):
        suppress_table([[10]], 0)


def test_the_organisation_may_raise_the_threshold_never_lower_it() -> None:
    assert effective_threshold(5, None) == 5
    assert effective_threshold(5, 12) == 12
    assert effective_threshold(5, 2) == 5
    assert effective_threshold(3, None) == 3


def test_a_platform_value_below_the_floor_is_raised_to_it() -> None:
    assert effective_threshold(1, None) == MIN_CELL_SIZE_FLOOR
    assert effective_threshold(0, 1) == MIN_CELL_SIZE_FLOOR


def test_a_small_cell_is_hidden_and_its_figures_blanked() -> None:
    cell = UsageCell(datetime(2026, 11, 9, 10, tzinfo=UTC), 1, 2, "Q5", 4, 3600, 1800)

    shown = show_cell(cell, THRESHOLD)

    assert shown.suppressed
    assert shown.count is None
    assert shown.sum_value is None
    assert shown.max_value is None


def test_a_cell_at_the_threshold_is_shown_whole() -> None:
    cell = UsageCell(datetime(2026, 11, 9, 10, tzinfo=UTC), 1, 2, "Q5", 5, 3600, 1800)

    shown = show_cell(cell, THRESHOLD)

    assert not shown.suppressed
    assert (shown.count, shown.sum_value, shown.max_value) == (5, 3600, 1800)


def test_a_line_without_small_cells_is_shown_in_full() -> None:
    view = suppress_line([5, 9, 40], THRESHOLD)

    assert view.cells == (5, 9, 40)
    assert view.total == 54


def test_one_small_cell_takes_the_next_smallest_with_it() -> None:
    """Otherwise the total minus the shown cells gives the hidden one back."""
    view = suppress_line([2, 30, 7, 50], THRESHOLD)

    assert view.cells == (None, 30, None, 50)
    assert view.total == 89


def test_two_small_cells_need_no_third() -> None:
    view = suppress_line([1, 2, 30, 50], THRESHOLD)

    assert view.cells == (None, None, 30, 50)


def test_a_total_below_the_threshold_is_not_shown() -> None:
    view = suppress_line([1, 2], THRESHOLD)

    assert view.total is None
    assert view.cells == (None, None)


def test_a_single_small_cell_hides_its_own_total() -> None:
    view = suppress_line([3], THRESHOLD)

    assert view.cells == (None,)
    assert view.total is None


def test_a_hidden_total_needs_no_complementary_cell() -> None:
    view = suppress_line([0, 4], THRESHOLD)

    assert view.total is None
    assert view.cells == (None, None)


def test_a_zero_count_is_a_small_cell_like_any_other() -> None:
    assert suppress_line([0, 20, 30], THRESHOLD).cells[0] is None


def test_the_tie_for_next_smallest_goes_to_the_first_cell() -> None:
    view = suppress_line([1, 9, 9, 40], THRESHOLD)

    assert view.cells == (None, None, 9, 40)


def test_a_negative_count_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        suppress_line([-1, 10], THRESHOLD)


def test_a_table_hides_small_cells_and_their_complements_in_rows_and_columns() -> None:
    view = suppress_table([[2, 30], [40, 50]], THRESHOLD)

    assert view.cells[0][0] is None
    assert sum(c is None for c in view.cells[0]) >= 2
    assert sum(view.cells[r][0] is None for r in range(2)) >= 2


def test_a_table_of_large_cells_is_shown_with_its_totals() -> None:
    view = suppress_table([[5, 6], [7, 8]], THRESHOLD)

    assert view.cells == ((5, 6), (7, 8))
    assert view.row_totals == (11, 15)
    assert view.col_totals == (12, 14)
    assert view.grand_total == 26


def test_a_table_with_rows_of_unequal_length_is_refused() -> None:
    with pytest.raises(ValueError, match="rectangular"):
        suppress_table([[1, 2], [3]], THRESHOLD)


def test_an_empty_table_has_nothing_to_show() -> None:
    view = suppress_table([], THRESHOLD)

    assert view.cells == ()
    assert view.grand_total is None


def _random_counts(rng: random.Random, size: int) -> list[int]:
    """Return counts that favour small cells, the dangerous case."""
    return [rng.choice([0, 1, 2, 3, 4, 5, 6, 9, 15, 40, 200]) for _ in range(size)]


def test_property_no_line_lets_one_hidden_cell_be_recovered_from_its_total() -> None:
    """R2: a shown total with a hidden cell always has a second hidden cell beside it."""
    rng = random.Random(117)  # noqa: S311
    for _ in range(2000):
        counts = _random_counts(rng, rng.randint(1, 8))
        threshold = rng.randint(3, 20)

        view = suppress_line(counts, threshold)

        hidden = sum(c is None for c in view.cells)
        if view.total is not None:
            assert hidden != 1, (counts, threshold)


def test_property_a_cell_below_the_threshold_is_never_shown() -> None:
    rng = random.Random(118)  # noqa: S311
    for _ in range(2000):
        counts = _random_counts(rng, rng.randint(1, 8))
        threshold = rng.randint(3, 20)

        view = suppress_line(counts, threshold)

        for original, shown in zip(counts, view.cells, strict=True):
            if original < threshold:
                assert shown is None


def test_property_a_shown_figure_is_the_true_figure() -> None:
    rng = random.Random(119)  # noqa: S311
    for _ in range(2000):
        counts = _random_counts(rng, rng.randint(1, 8))
        threshold = rng.randint(3, 20)

        view = suppress_line(counts, threshold)

        for original, shown in zip(counts, view.cells, strict=True):
            assert shown is None or shown == original
        assert view.total in (None, sum(counts))
        assert view.total is None or view.total >= threshold


def test_property_a_table_leaks_no_single_cell_through_a_row_or_column() -> None:
    rng = random.Random(120)  # noqa: S311
    for _ in range(1500):
        rows = rng.randint(1, 5)
        cols = rng.randint(1, 5)
        table = [_random_counts(rng, cols) for _ in range(rows)]
        threshold = rng.randint(3, 20)

        view = suppress_table(table, threshold)

        for r in range(rows):
            hidden = sum(c is None for c in view.cells[r])
            if view.row_totals[r] is not None:
                assert hidden != 1, (table, threshold, "row", r)
        for c in range(cols):
            hidden = sum(view.cells[r][c] is None for r in range(rows))
            if view.col_totals[c] is not None:
                assert hidden != 1, (table, threshold, "col", c)
        for r in range(rows):
            for c in range(cols):
                if table[r][c] < threshold:
                    assert view.cells[r][c] is None
                else:
                    assert view.cells[r][c] in (None, table[r][c])


def test_a_cell_hidden_by_its_column_takes_the_row_total_with_it() -> None:
    """The case the property test found: the row total of one cell is that cell."""
    view = suppress_table([[15], [0], [200]], 7)

    assert view.cells == ((None,), (None,), (200,))
    assert view.row_totals[0] is None
    assert view.col_totals == (215,)


def test_a_table_total_stays_consistent_with_what_is_shown() -> None:
    view = suppress_table([[5, 6], [7, 8]], THRESHOLD)
    small = suppress_table([[1, 1], [1, 1]], THRESHOLD)

    assert view.grand_total == 26
    assert small.grand_total is None
    assert all(c is None for row in small.cells for c in row)


def test_property_the_grand_total_never_leaves_exactly_one_cell_hidden() -> None:
    rng = random.Random(122)  # noqa: S311
    for _ in range(1500):
        rows, cols = rng.randint(1, 5), rng.randint(1, 5)
        table = [_random_counts(rng, cols) for _ in range(rows)]
        threshold = rng.randint(3, 20)

        view = suppress_table(table, threshold)

        if view.grand_total is not None:
            assert sum(c is None for r in view.cells for c in r) != 1, (table, threshold)
