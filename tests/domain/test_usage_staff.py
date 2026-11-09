"""The cells of a team member's facts, and the closed list of what such a fact can be (point 9)."""

from datetime import UTC, datetime

import pytest

from app.domain.usage import FactKind, StaffFact
from app.domain.usage_staff import (
    CALLS,
    CLAIMS,
    PUNCTUAL,
    fold,
    merge_staff,
    roll_staff,
    staff_cells_of,
)

HOUR = datetime(2026, 11, 18, 10, tzinfo=UTC)


def _fact(kind: FactKind, seconds: int | None = None, staff_id: int = 7) -> StaffFact:
    return StaffFact(kind, HOUR, 1, 2, staff_id, seconds)


def test_a_call_that_ended_is_one_call_with_its_length() -> None:
    [cell] = staff_cells_of(_fact(FactKind.CALL_ENDED, 600))

    assert (cell.metric, cell.count, cell.sum_value, cell.staff_id) == (CALLS, 1, 600, 7)


def test_a_call_without_its_length_still_counts_as_a_call() -> None:
    [cell] = staff_cells_of(_fact(FactKind.CALL_ENDED, None))

    assert (cell.count, cell.sum_value) == (1, 0)


def test_a_claim_is_one_claim() -> None:
    [cell] = staff_cells_of(_fact(FactKind.QUEUE_CLAIMED))

    assert (cell.metric, cell.count) == (CLAIMS, 1)


@pytest.mark.parametrize(("seconds", "on_time"), [(-30, 1), (0, 1), (45, 0)])
def test_the_host_is_on_time_at_or_before_the_start(seconds: int, on_time: int) -> None:
    [cell] = staff_cells_of(_fact(FactKind.CALL_HOST_JOINED, seconds))

    assert (cell.metric, cell.count, cell.sum_value) == (PUNCTUAL, 1, on_time)


def test_a_host_join_without_a_start_to_be_late_against_makes_no_cell() -> None:
    assert staff_cells_of(_fact(FactKind.CALL_HOST_JOINED, None)) == []


def test_a_fact_kind_outside_metric_s1_is_refused() -> None:
    with pytest.raises(ValueError, match="metric S1"):
        _fact(FactKind.BOOKING_CREATED)


def test_cells_of_the_same_hour_group_member_and_metric_add_up() -> None:
    merged = roll_staff(
        [
            _fact(FactKind.CALL_ENDED, 60),
            _fact(FactKind.CALL_ENDED, 120),
            _fact(FactKind.CALL_ENDED, 90, 8),
        ]
    )

    by_member = {c.staff_id: (c.count, c.sum_value, c.max_value) for c in merged}
    assert by_member == {7: (2, 180, 120), 8: (1, 90, 90)}


def test_folding_removes_the_person_and_keeps_the_totals() -> None:
    cells = merge_staff(
        c
        for f in (_fact(FactKind.CALL_ENDED, 60), _fact(FactKind.CALL_ENDED, 120, 8))
        for c in staff_cells_of(f)
    )

    [former] = fold(cells)

    assert former.staff_id is None
    assert (former.count, former.sum_value) == (2, 180)
