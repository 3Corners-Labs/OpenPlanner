"""Small-cell rules of reporting on use (ADR 0117 point 8, abuse case R2; pure functions).

Every read of the statistics (screen, CSV, API, digest) goes through this module, so none of
them holds its own rule. A cell below the threshold shows no count, sum or maximum. A row or
column total is shown only when it is itself at least the threshold and showing it would not
give a hidden cell back, which is why one hidden cell takes the next smallest with it.

    counts ─► ``show_cell`` (one cell)            ─► below the threshold: blanked
           ─► ``suppress_line`` (a row or column) ─► small cells and one complement hidden
           ─► ``suppress_table`` (two axes)       ─► the same, along both axes

A set of cells shown together with a total always goes through ``suppress_line`` or
``suppress_table``; ``show_cell`` alone is for one cell and never for a table. Suppression runs
on the figures of the range the reader asked for, after they are added up, never on the hourly
rollup cells. The range rules that stop two adjacent ranges being subtracted into one hour
(whole days, hour cuts over seven days or more, the group-total rule) belong to the query layer of
step 5. Residual limit, as with every complementary suppression: a hidden cell is known to be
below the threshold, so a total can bound it.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.usage import UsageCell

MIN_CELL_SIZE_FLOOR = 3
"""The smallest small-cell threshold there is: a protection, so it cannot be set lower."""


def effective_threshold(platform: int, organisation: int | None = None) -> int:
    """Return the small-cell threshold that applies: the largest of the floor and the settings.

    An organisation may raise the threshold for its own view and never lower it, and a later
    rise of the platform value applies at once, so the larger of the two wins (ADR 0117
    point 8). A platform value below the floor is raised to it.
    """
    return max(MIN_CELL_SIZE_FLOOR, platform, organisation or 0)


def _check_threshold(threshold: int) -> None:
    """Refuse a threshold below the floor: the protection cannot be switched off."""
    if threshold < MIN_CELL_SIZE_FLOOR:
        raise ValueError(f"threshold is below the floor of {MIN_CELL_SIZE_FLOOR}")


@dataclass(frozen=True)
class ShownCell:
    """A cell as a reader may see it: whole, or hidden with its figures blanked.

    Attributes:
        count: The count, or ``None`` when the cell is below the threshold.
        sum_value: The sum, blanked with the count.
        max_value: The largest value, blanked with the count.
        suppressed: Whether the cell is hidden; the interface writes ``< 5`` for it.
    """

    count: int | None
    sum_value: int | None
    max_value: int | None
    suppressed: bool


def show_cell(cell: UsageCell, threshold: int) -> ShownCell:
    """Return ``cell`` as a reader may see it: below ``threshold`` count, sum and maximum go.

    Raises:
        ValueError: If ``threshold`` is below the floor of 3.
    """
    _check_threshold(threshold)
    if cell.count < threshold:
        return ShownCell(None, None, None, True)
    return ShownCell(cell.count, cell.sum_value, cell.max_value, False)


@dataclass(frozen=True)
class LineView:
    """A row or column after suppression.

    Attributes:
        cells: Each count, or ``None`` where it is hidden.
        total: The total, or ``None`` when it is below the threshold.
    """

    cells: tuple[int | None, ...]
    total: int | None


def _complement(counts: Sequence[int], hidden: list[bool], threshold: int) -> bool:
    """Hide the next smallest cell when one cell of a line with a shown total is hidden.

    Returns whether it changed anything. The total is shown only from the threshold up; with
    exactly one hidden cell the total minus the shown cells would give it back.
    """
    if sum(counts) < threshold or sum(hidden) != 1:
        return False
    rest = [i for i, h in enumerate(hidden) if not h]
    if not rest:
        return False
    hidden[min(rest, key=lambda i: (counts[i], i))] = True
    return True


def _shown_total(counts: Sequence[int], hidden: Sequence[bool], threshold: int) -> int | None:
    """Return a line's total, or ``None`` when showing it would give a hidden cell back.

    A total is shown only from the threshold up, and never when exactly one cell of its line is
    hidden and no other cell is left to hide with it (a cell hidden by the other axis of a
    table): the total minus the shown cells would be that cell.
    """
    total = sum(counts)
    if total < threshold or sum(hidden) == 1:
        return None
    return total


def suppress_line(counts: Sequence[int], threshold: int) -> LineView:
    """Hide the small cells of a row or column, and one more when a single one would show.

    A cell below the threshold is hidden. When exactly one cell is hidden and the total is
    shown, the next smallest cell is hidden too, so the total cannot be subtracted back. The
    total is shown only if it is itself at least the threshold.

    Raises:
        ValueError: If ``threshold`` is below the floor, or a count is negative.
    """
    _check_threshold(threshold)
    if any(c < 0 for c in counts):
        raise ValueError("a count is negative")
    hidden = [c < threshold for c in counts]
    _complement(counts, hidden, threshold)
    return LineView(
        tuple(None if h else c for c, h in zip(counts, hidden, strict=True)),
        _shown_total(counts, hidden, threshold),
    )


@dataclass(frozen=True)
class TableView:
    """A table after suppression, with the totals that may be shown.

    Attributes:
        cells: Rows of counts, ``None`` where hidden.
        row_totals: Each row total, ``None`` below the threshold or when it would reveal a cell.
        col_totals: Each column total, ``None`` for the same reasons.
        grand_total: The grand total, ``None`` for the same reasons.
    """

    cells: tuple[tuple[int | None, ...], ...]
    row_totals: tuple[int | None, ...]
    col_totals: tuple[int | None, ...]
    grand_total: int | None


def suppress_table(rows: Sequence[Sequence[int]], threshold: int) -> TableView:
    """Hide the small cells of a table and complement them along rows and columns.

    Every row and column with a shown total ends with no or at least two hidden cells, so
    no cell is recovered from a total. Rows are checked, then columns, until nothing changes;
    suppression only ever grows, so this ends.

    Raises:
        ValueError: If ``threshold`` is below the floor, a count is negative, or the rows are
            not all of the same length.
    """
    _check_threshold(threshold)
    width = len(rows[0]) if rows else 0
    if any(len(r) != width for r in rows):
        raise ValueError("the table is not rectangular")
    if any(c < 0 for r in rows for c in r):
        raise ValueError("a count is negative")
    hidden = [[c < threshold for c in r] for r in rows]
    changed = True
    while changed:
        changed = False
        for i, r in enumerate(rows):
            changed |= _complement(r, hidden[i], threshold)
        for j in range(width):
            column = [r[j] for r in rows]
            flags = [h[j] for h in hidden]
            if _complement(column, flags, threshold):
                for i, flag in enumerate(flags):
                    hidden[i][j] = flag
                changed = True
    row_totals = tuple(_shown_total(r, hidden[i], threshold) for i, r in enumerate(rows))
    col_totals = tuple(
        _shown_total([r[j] for r in rows], [h[j] for h in hidden], threshold) for j in range(width)
    )
    every = [c for r in rows for c in r]
    flat = [h for r in hidden for h in r]
    return TableView(
        tuple(
            tuple(None if h else c for c, h in zip(r, hidden[i], strict=True))
            for i, r in enumerate(rows)
        ),
        row_totals,
        col_totals,
        _shown_total(every, flat, threshold) if rows else None,
    )
