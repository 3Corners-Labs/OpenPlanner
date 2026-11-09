"""The Capacity report: utilisation, fill, busiest hours, shortfall (ADR 0117 step 6, R2)."""

from datetime import UTC, date, datetime

from app.domain.usage import UsageCell
from app.domain.usage_report import ReportGroup
from app.domain.usage_report_capacity import capacity_report

GROUP = ReportGroup(1, 1, "balie", "Balie", "demo", "Demo", "Europe/Amsterdam")
SMALL = ReportGroup(2, 1, "intake", "Intake", "demo", "Demo", "Europe/Amsterdam")
FIRST = date(2026, 11, 2)
LAST = date(2026, 11, 8)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
HOUR = datetime(2026, 11, 4, 9, tzinfo=UTC)


def _slots(
    slots: int, booked: int, capacity: int, hour: datetime = HOUR, group: int = 1
) -> list[UsageCell]:
    return [
        UsageCell(hour, 1, group, "C1", slots, booked, booked, reason_code="booked"),
        UsageCell(hour, 1, group, "C1", slots, capacity, capacity, reason_code="capacity"),
        UsageCell(hour, 1, group, "C3", slots, capacity - booked, capacity - booked),
    ]


def _report(cells: list[UsageCell], groups: list[ReportGroup] | None = None, last: date = LAST):  # type: ignore[no-untyped-def]
    return capacity_report(cells, groups or [GROUP], FIRST, last, threshold=5, as_of=NOW)


def test_without_cells_the_report_is_empty() -> None:
    report = _report([])

    assert report.empty
    assert report.utilisation_percent is None
    assert all(d.slots is None for d in report.days)


def test_utilisation_is_booked_over_capacity_per_day_and_in_total() -> None:
    cells = _slots(10, 30, 40) + _slots(10, 10, 40, datetime(2026, 11, 5, 9, tzinfo=UTC))

    report = _report(cells)

    by_day = {d.day: d for d in report.days}
    assert by_day[date(2026, 11, 4)].utilisation_percent == 75
    assert by_day[date(2026, 11, 5)].utilisation_percent == 25
    assert by_day[date(2026, 11, 4)].idle == 10
    assert report.slots == 20
    assert report.utilisation_percent == 50
    assert report.idle == 40


def test_few_slots_in_a_day_hide_that_day() -> None:
    cells = _slots(3, 3, 6) + _slots(20, 10, 20, datetime(2026, 11, 5, 9, tzinfo=UTC))

    by_day = {d.day: d for d in _report(cells).days}

    assert by_day[date(2026, 11, 4)].slots is None
    assert by_day[date(2026, 11, 4)].utilisation_percent is None
    assert by_day[date(2026, 11, 4)].idle is None


def test_a_percentage_of_a_few_bookings_is_a_small_cell_too() -> None:
    cells = _slots(20, 2, 40)

    day = {d.day: d for d in _report(cells).days}[date(2026, 11, 4)]

    assert day.slots == 20
    assert day.utilisation_percent is None
    assert day.idle is None


def test_an_empty_day_with_many_slots_is_zero_percent() -> None:
    day = {d.day: d for d in _report(_slots(20, 0, 40)).days}[date(2026, 11, 4)]

    assert day.utilisation_percent == 0
    assert day.idle == 40


def test_fill_classes_and_shortfall_are_counted() -> None:
    cells = [
        UsageCell(HOUR, 1, 1, "C2", 12, 0, 0, reason_code="empty"),
        UsageCell(HOUR, 1, 1, "C2", 30, 0, 0, reason_code="full"),
        UsageCell(HOUR, 1, 1, "C5", 8, 0, 0, reason_code="accepted"),
    ]

    report = _report(cells)

    assert [r.count for r in report.fill.rows] == [12, None, None, 30] or report.fill.rows[
        3
    ].count == 30
    assert [r.code for r in report.shortfall.rows] == ["open", "accepted", "resolved"]
    assert [r.count for r in report.shortfall.rows] == [None, 8, None]


def _bookings(count: int, hour: datetime, group: int = 1) -> UsageCell:
    return UsageCell(hour, 1, group, "C4", count, 0, 0)


def test_the_busiest_hours_need_a_range_of_seven_days() -> None:
    cells = [_bookings(40, HOUR)]

    short = _report(cells, last=date(2026, 11, 7)).heat
    full = _report(cells).heat

    assert not short.offered
    assert full.offered
    assert full.hours == (10,)  # 09:00 UTC is 10:00 in Amsterdam in November
    assert full.cells[0][2] == 40  # Wednesday
    assert full.levels[0][2] == 4


def test_a_group_below_five_times_the_threshold_is_not_cut_by_hour() -> None:
    cells = [_bookings(40, HOUR), _bookings(20, HOUR, group=2)]

    heat = _report(cells, [GROUP, SMALL]).heat

    assert heat.groups_left_out == 1
    assert heat.cells[0][2] == 40


def test_weekday_and_hour_are_the_groups_own_local_time() -> None:
    late = datetime(2026, 11, 4, 23, 30, tzinfo=UTC)  # Thursday 00:30 in Amsterdam

    heat = _report([_bookings(40, late)]).heat

    assert heat.hours == (0,)
    assert heat.cells[0][3] == 40


def test_shade_is_relative_to_the_largest_shown_count() -> None:
    cells = [_bookings(100, HOUR), _bookings(30, datetime(2026, 11, 5, 9, tzinfo=UTC))]

    heat = _report(cells).heat

    assert heat.levels[0][2] == 4
    assert heat.levels[0][3] == 2


def test_hidden_cells_have_no_shade() -> None:
    cells = [_bookings(60, HOUR), _bookings(2, datetime(2026, 11, 5, 9, tzinfo=UTC))]

    heat = _report(cells).heat

    assert heat.cells[0][3] is None
    assert heat.levels[0][3] == 0
