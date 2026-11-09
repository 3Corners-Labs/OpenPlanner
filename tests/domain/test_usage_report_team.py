"""The Team report: one row per team member by name, no ranking, small figures hidden (point 9)."""

from datetime import UTC, date, datetime

from app.domain.usage import FactKind, StaffFact
from app.domain.usage_report import ReportGroup
from app.domain.usage_report_team import team_report
from app.domain.usage_staff import StaffCell, roll_staff

DAY = date(2026, 11, 18)
HOUR = datetime(2026, 11, 18, 10, tzinfo=UTC)
GROUP = ReportGroup(2, 1, "alfa", "Alfa", "org", "Org", "Europe/Amsterdam")
NAMES = {1: "Zoë", 2: "anna", 3: "Bram"}


def _cells(*facts: StaffFact) -> list[StaffCell]:
    return roll_staff(list(facts))


def _calls(staff_id: int, n: int, seconds: int = 600) -> list[StaffFact]:
    return [StaffFact(FactKind.CALL_ENDED, HOUR, 1, 2, staff_id, seconds) for _ in range(n)]


def _report(cells: list[StaffCell], threshold: int = 5, **values: object):  # type: ignore[no-untyped-def]
    return team_report(
        cells,
        NAMES,
        [GROUP],
        DAY,
        DAY,
        threshold=threshold,
        as_of=None,
        **values,  # type: ignore[arg-type]
    )


def test_the_rows_are_sorted_by_name_and_not_by_any_figure() -> None:
    cells = _cells(*_calls(1, 9), *_calls(2, 5), *_calls(3, 20))

    report = _report(cells)

    assert [r.name for r in report.rows] == ["anna", "Bram", "Zoë"]
    assert [r.calls for r in report.rows] == [5, 20, 9]


def test_a_member_with_fewer_calls_than_the_threshold_shows_none_of_the_call_figures() -> None:
    cells = _cells(*_calls(1, 4), *_calls(2, 6))

    by_name = {r.name: r for r in _report(cells).rows}

    assert (by_name["Zoë"].calls, by_name["Zoë"].call_minutes) == (None, None)
    assert (by_name["anna"].calls, by_name["anna"].call_minutes) == (6, 60)


def test_claims_and_on_time_are_hidden_on_their_own_threshold() -> None:
    facts = [
        *_calls(1, 6),
        *[StaffFact(FactKind.QUEUE_CLAIMED, HOUR, 1, 2, 1) for _ in range(3)],
        *[StaffFact(FactKind.CALL_HOST_JOINED, HOUR, 1, 2, 1, -10) for _ in range(4)],
        *[StaffFact(FactKind.CALL_HOST_JOINED, HOUR, 1, 2, 1, 120) for _ in range(4)],
    ]

    [row] = _report(_cells(*facts)).rows

    assert (row.calls, row.claims, row.on_time_percent) == (6, None, 50)


def test_the_on_time_share_needs_enough_scheduled_calls() -> None:
    facts = [
        *_calls(1, 6),
        *[StaffFact(FactKind.CALL_HOST_JOINED, HOUR, 1, 2, 1, -10) for _ in range(4)],
    ]

    [row] = _report(_cells(*facts)).rows

    assert row.on_time_percent is None


def test_removed_team_members_are_one_row_at_the_end() -> None:
    former = StaffCell(HOUR, 1, 2, None, "S1c", 12, 7200, 600)

    report = _report([*_cells(*_calls(3, 5)), former])

    assert [r.name for r in report.rows] == ["Bram", ""]
    assert report.rows[-1].former
    assert report.rows[-1].calls == 12


def test_a_group_outside_the_report_is_left_out() -> None:
    other = StaffCell(HOUR, 1, 99, 1, "S1c", 50, 100, 5)

    report = _report([other])

    assert report.empty
    assert report.rows == ()


def test_the_days_are_the_groups_own() -> None:
    late = datetime(2026, 11, 18, 23, tzinfo=UTC)  # 00:00 on the 19th in Amsterdam
    cell = StaffCell(late, 1, 2, 1, "S1c", 9, 0, 0)

    assert _report([cell]).empty
