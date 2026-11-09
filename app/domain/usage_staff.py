"""The hourly cells of a team member's facts (ADR 0117 point 9).

Everything about a person lives here and in the one table it feeds (``usage_staff_hour``); the
other cells never hold a ``staff_id``:

    StaffFact ─► ``staff_cells_of`` (one cell per metric of S1 the fact feeds)
              ─► ``merge_staff`` (same hour, group, team member and metric add up)
              ─► ``StaffCell`` rows for ``usage_staff_hour``

    a removed team member ─► ``fold`` (the member's cells become *former team members*, the
                                       ``staff_id`` of the cell is gone and the sums stay)

The facts themselves are written by ``app.domain.staff_usage`` while the organisation has the
team view on (``reporting_staff_view_enabled``, class D, off by default).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from app.domain.usage import FactKind, StaffFact, UsageFact

CALLS = "S1c"
"""Calls handled: one per call that ended, and its length in seconds as the figure."""
CLAIMS = "S1k"
"""Claims from the own queue: one per claim."""
PUNCTUAL = "S1p"
"""Scheduled calls the host joined: one per call, with a figure of one when on time, else zero."""
STAFF_METRICS: tuple[str, ...] = (CALLS, CLAIMS, PUNCTUAL)
"""The metrics of ``usage_staff_hour`` (the sub-ids of S1, which the catalogue lists as one)."""


@dataclass(frozen=True)
class StaffCell:
    """One hour of one metric of one team member, as ``usage_staff_hour`` stores it.

    Attributes:
        bucket_hour: The UTC hour.
        organisation_id: The organisation.
        group_id: The group.
        staff_id: The row id of the ``staff`` entry; ``None`` is *former team members*.
        metric: One of ``STAFF_METRICS``.
        count: The facts in the hour.
        sum_value: The sum of their figures.
        max_value: The largest figure.
    """

    bucket_hour: datetime
    organisation_id: int
    group_id: int
    staff_id: int | None
    metric: str
    count: int
    sum_value: int
    max_value: int


def staff_cells_of(fact: StaffFact) -> list[StaffCell]:
    """Return the cells one team member's fact feeds (none for a fact without its figure)."""

    def cell(metric: str, figure: int) -> StaffCell:
        return StaffCell(
            fact.bucket_hour,
            fact.organisation_id,
            fact.group_id,
            fact.staff_id,
            metric,
            1,
            figure,
            figure,
        )

    if fact.kind is FactKind.CALL_ENDED:
        return [cell(CALLS, max(fact.value_seconds or 0, 0))]
    if fact.kind is FactKind.QUEUE_CLAIMED:
        return [cell(CLAIMS, 0)]
    if fact.kind is FactKind.CALL_HOST_JOINED and fact.value_seconds is not None:
        return [cell(PUNCTUAL, 1 if fact.value_seconds <= 0 else 0)]
    return []


def merge_staff(cells: Iterable[StaffCell]) -> list[StaffCell]:
    """Return ``cells`` with those of the same hour, group, member and metric added together."""
    merged: dict[tuple[datetime, int, int, int | None, str], StaffCell] = {}
    for cell in cells:
        key = (cell.bucket_hour, cell.organisation_id, cell.group_id, cell.staff_id, cell.metric)
        found = merged.get(key)
        merged[key] = (
            cell
            if found is None
            else replace(
                found,
                count=found.count + cell.count,
                sum_value=found.sum_value + cell.sum_value,
                max_value=max(found.max_value, cell.max_value),
            )
        )
    return list(merged.values())


def roll_staff(facts: Sequence[UsageFact | StaffFact]) -> list[StaffCell]:
    """Return the merged cells of the team members' facts among ``facts``."""
    return merge_staff(c for f in facts if isinstance(f, StaffFact) for c in staff_cells_of(f))


def fold(cells: Iterable[StaffCell]) -> list[StaffCell]:
    """Return ``cells`` as *former team members*: the person is gone, the totals stay."""
    return merge_staff(replace(c, staff_id=None) for c in cells)


def facts_folded(facts: Iterable[StaffFact]) -> list[StaffCell]:
    """Return the cells that facts still waiting for the rollup make once their member is gone."""
    return fold(c for f in facts for c in staff_cells_of(f))
