"""The *Team* report: the load per team member (ADR 0117 point 9, metric S1).

A pure function from the team members' hourly cells to a table with one row per team member. It
holds no database and no text. What keeps it from being a performance league:

- the rows are sorted by name, with no figure to sort by and no *top* or *worst* list;
- there is one table for the whole organisation and no cut by group, subject or hour; ranges
  that overlap can still differ by a small amount, a residual risk every report here shares;
- every figure below the threshold reads ``< n`` and the figures that depend on it go with it:
  call minutes need the calls to be shown, the on-time share needs enough scheduled calls;
- there is no total row;
- the team members who have been removed are one row, *former team members*, at the end.

    cells ─► per member: calls, minutes, claims, on time ─► ``TeamReport`` (small cells hidden)
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from app.domain.usage_report import ReportGroup, local_cells, zones_of
from app.domain.usage_staff import CALLS, CLAIMS, PUNCTUAL, StaffCell


@dataclass(frozen=True)
class TeamRow:
    """One team member's load in the range, with small figures already hidden.

    Attributes:
        staff_id: The team member's row id; ``None`` for *former team members*.
        name: The display name; empty for *former team members*, which the page names.
        calls: Calls handled, ``None`` when below the threshold.
        call_minutes: Minutes in those calls, whole minutes; ``None`` with the calls.
        claims: Claims from the own queue, ``None`` when below the threshold.
        on_time_percent: Scheduled calls the host joined on time, in whole percent; ``None``
            when fewer than the threshold scheduled calls were seen.
    """

    staff_id: int | None
    name: str
    calls: int | None
    call_minutes: int | None
    claims: int | None
    on_time_percent: int | None

    @property
    def former(self) -> bool:
        """Whether this is the row of the team members who have been removed."""
        return self.staff_id is None


@dataclass(frozen=True)
class TeamReport:
    """Everything the *Team* screen shows.

    Attributes:
        first_day: The first day of the range (group-local).
        last_day: The last day of the range.
        as_of: When the rollup last ran.
        threshold: The small-cell threshold that was applied.
        rows: One row per team member by name, then *former team members* if there are any.
        empty: Whether the range holds no cell of a team member at all.
    """

    first_day: date
    last_day: date
    as_of: datetime | None
    threshold: int
    rows: tuple[TeamRow, ...]
    empty: bool


@dataclass
class _Sums:
    """What one team member's cells add up to."""

    calls: int = 0
    seconds: int = 0
    claims: int = 0
    scheduled: int = 0
    on_time: int = 0


def _shown(count: int, threshold: int) -> int | None:
    """Return ``count``, or ``None`` when it is a small cell."""
    return count if count >= threshold else None


def _row(staff_id: int | None, name: str, sums: _Sums, threshold: int) -> TeamRow:
    """Return one row from a member's sums, each figure through the small-cell rule."""
    calls = _shown(sums.calls, threshold)
    on_time = _shown(sums.scheduled, threshold)
    return TeamRow(
        staff_id,
        name,
        calls,
        round(sums.seconds / 60) if calls is not None else None,
        _shown(sums.claims, threshold),
        round(100 * sums.on_time / sums.scheduled) if on_time is not None else None,
    )


def team_report(
    cells: Sequence[StaffCell],
    names: Mapping[int, str],
    groups: Sequence[ReportGroup],
    first_day: date,
    last_day: date,
    *,
    threshold: int,
    as_of: datetime | None,
) -> TeamReport:
    """Return the *Team* report of the cells whose group-local day lies in the range.

    Args:
        cells: The team members' hourly cells of ``groups``.
        names: The display name of each team member that still has one.
        groups: The groups the report adds up; a cell of any other group is left out.
        first_day: The first group-local day.
        last_day: The last group-local day.
        threshold: The small-cell threshold of the organisation (``effective_threshold``).
        as_of: When the rollup last ran.
    """
    sums: dict[int | None, _Sums] = defaultdict(_Sums)
    for cell, _local in local_cells(cells, zones_of(groups), first_day, last_day):
        total = sums[cell.staff_id]
        if cell.metric == CALLS:
            total.calls += cell.count
            total.seconds += cell.sum_value
        elif cell.metric == CLAIMS:
            total.claims += cell.count
        elif cell.metric == PUNCTUAL:
            total.scheduled += cell.count
            total.on_time += cell.sum_value
    members = sorted(
        ((i, names.get(i, "")) for i in sums if i is not None),
        key=lambda item: (item[1].casefold(), item[0]),
    )
    rows = [_row(i, name, sums[i], threshold) for i, name in members]
    if None in sums:
        rows.append(_row(None, "", sums[None], threshold))
    return TeamReport(first_day, last_day, as_of, threshold, tuple(rows), not sums)
