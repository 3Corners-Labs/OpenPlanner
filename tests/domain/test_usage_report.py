"""The Volume report: tables from hourly cells, with small cells hidden (ADR 0117 step 5, R2)."""

from datetime import UTC, date, datetime

from app.domain.booking import BookingStatus
from app.domain.usage import ActorClass, UsageCell, UsageOutcome, duration_bucket
from app.domain.usage_report import (
    UNKNOWN,
    DayStatusCount,
    ReportGroup,
    days_in,
    volume_report,
)

GROUP = ReportGroup(1, 1, "balie", "Balie", "demo", "Demo", "Europe/Amsterdam")
OTHER = ReportGroup(2, 1, "intake", "Intake", "demo", "Demo", "America/New_York")
FIRST = date(2026, 11, 2)
LAST = date(2026, 11, 8)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
THRESHOLD = 5


def _cell(
    metric: str,
    *,
    count: int = 10,
    hour: datetime = datetime(2026, 11, 4, 10, tzinfo=UTC),
    group: int = 1,
    total: int = 0,
    **cuts: object,
) -> UsageCell:
    return UsageCell(hour, 1, group, metric, count, total, total, **cuts)  # type: ignore[arg-type]


def _report(cells: list[UsageCell], groups: list[ReportGroup] | None = None, **extra: object):  # type: ignore[no-untyped-def]
    return volume_report(
        cells,
        groups or [GROUP],
        FIRST,
        LAST,
        threshold=THRESHOLD,
        now=NOW,
        as_of=NOW,
        **extra,  # type: ignore[arg-type]
    )


def test_days_in_a_range_are_all_listed() -> None:
    assert days_in(date(2026, 11, 1), date(2026, 11, 3)) == [
        date(2026, 11, 1),
        date(2026, 11, 2),
        date(2026, 11, 3),
    ]


def test_bookings_created_are_counted_per_group_local_day() -> None:
    cells = [
        _cell("U1", count=12, hour=datetime(2026, 11, 4, 8, tzinfo=UTC)),
        _cell("U1", count=9, hour=datetime(2026, 11, 4, 14, tzinfo=UTC)),
        _cell("U1", count=30, hour=datetime(2026, 11, 5, 9, tzinfo=UTC)),
    ]

    report = _report(cells)

    by_day = {row.day: row.count for row in report.days}
    assert by_day[date(2026, 11, 4)] == 21
    assert by_day[date(2026, 11, 5)] == 30
    assert report.created_total == 51


def test_a_late_evening_hour_belongs_to_the_next_local_day() -> None:
    """23:30 UTC on 4 November is 00:30 on 5 November in Amsterdam."""
    report = _report([_cell("U1", count=20, hour=datetime(2026, 11, 4, 23, tzinfo=UTC))])

    by_day = {row.day: row.count for row in report.days}
    assert by_day[date(2026, 11, 5)] == 20
    assert by_day[date(2026, 11, 4)] is None


def test_each_group_has_its_own_time_zone() -> None:
    hour = datetime(2026, 11, 5, 2, tzinfo=UTC)  # 03:00 in Amsterdam, 21:00 the day before in NY
    report = _report(
        [_cell("U1", count=10, hour=hour, group=1), _cell("U1", count=10, hour=hour, group=2)],
        [GROUP, OTHER],
    )

    by_day = {row.day: row.count for row in report.days}
    assert by_day[date(2026, 11, 5)] == 10
    assert by_day[date(2026, 11, 4)] == 10


def test_hours_outside_the_range_and_other_groups_do_not_count() -> None:
    cells = [
        _cell("U1", count=40, hour=datetime(2026, 10, 30, 10, tzinfo=UTC)),
        _cell("U1", count=40, hour=datetime(2026, 11, 20, 10, tzinfo=UTC)),
        _cell("U1", count=40, group=99),
        _cell("U1", count=11),
    ]

    assert _report(cells).created_total == 11


def test_a_day_below_the_threshold_is_hidden_with_its_neighbour() -> None:
    counts = [10, 10, 2, 6, 40, 10, 10]  # 2 to 8 November
    cells = [
        _cell("U1", count=n, hour=datetime(2026, 11, 2 + i, 10, tzinfo=UTC))
        for i, n in enumerate(counts)
    ]

    report = _report(cells)

    shown = {row.day: row.count for row in report.days}
    assert shown[date(2026, 11, 4)] is None
    assert shown[date(2026, 11, 5)] is None  # the next smallest goes with it
    assert shown[date(2026, 11, 6)] == 40
    assert report.created_total == sum(counts)


def test_a_total_below_the_threshold_is_not_shown() -> None:
    assert _report([_cell("U1", count=3)]).created_total is None
    assert _report([]).created_total is None


def test_the_latest_day_is_provisional() -> None:
    report = _report([])

    provisional = [row.day for row in report.days if row.provisional]
    assert provisional == [date(2026, 11, 8)]


def test_outcomes_are_counted_per_outcome_in_a_fixed_order() -> None:
    cells = [
        _cell("U2", count=20, outcome=UsageOutcome.CANCELLED_BY_PLANNER),
        _cell("U2", count=7, outcome=UsageOutcome.CANCELLED_BY_SYSTEM),
        _cell("U2", count=1, outcome=UsageOutcome.EXPIRED_UNCONFIRMED),
    ]

    table = _report(cells).outcomes

    assert [r.code for r in table.rows] == [o.value for o in UsageOutcome]
    counts = {r.code: r.count for r in table.rows}
    assert counts["cancelled_by_planner"] == 20
    assert counts["cancelled_by_system"] == 7
    assert counts["expired_unconfirmed"] is None
    assert counts["completed"] is None
    assert table.total == 28


def test_lead_time_is_counted_per_bucket_with_an_exact_mean() -> None:
    hour, three_days = 3600, 3 * 86_400
    cells = [
        _cell("U6", count=10, total=10 * hour, bucket=duration_bucket(hour)),
        _cell("U6", count=10, total=10 * three_days, bucket=duration_bucket(three_days)),
    ]

    report = _report(cells)

    shown = {r.code: r.count for r in report.lead_time.rows if r.count is not None}
    assert shown == {str(duration_bucket(hour)): 10, str(duration_bucket(three_days)): 10}
    assert report.lead_mean_minutes == (10 * hour + 10 * three_days) // 20 // 60


def test_the_mean_is_hidden_when_the_total_is_hidden() -> None:
    report = _report([_cell("U6", count=2, total=2 * 3600, bucket=5)])

    assert report.lead_time.total is None
    assert report.lead_mean_minutes is None


def test_participant_cancellations_are_counted_per_time_left_before_the_start() -> None:
    cells = [
        _cell("U5", count=12, total=0, bucket=duration_bucket(1800)),
        _cell("U5", count=30, total=0, bucket=duration_bucket(3 * 86_400)),
    ]

    table = _report(cells).participant_cancellations

    shown = {r.code: r.count for r in table.rows if r.count is not None}
    assert shown == {str(duration_bucket(1800)): 12, str(duration_bucket(3 * 86_400)): 30}
    assert len(table.rows) == 10


def test_reschedules_are_per_actor_and_an_unnamed_actor_is_unknown() -> None:
    cells = [
        _cell("U7", count=15, actor_class=ActorClass.PLANNER),
        _cell("U7", count=15, actor_class=None),
    ]

    counts = {r.code: r.count for r in _report(cells).reschedules.rows}

    assert counts["planner"] == 15
    assert counts[UNKNOWN] == 15


def test_confirmation_conversion_needs_both_cells_and_the_total() -> None:
    both = _report(
        [
            _cell("U9", count=30),
            _cell("U9", count=10, outcome=UsageOutcome.EXPIRED_UNCONFIRMED),
        ]
    )
    small = _report(
        [_cell("U9", count=30), _cell("U9", count=1, outcome=UsageOutcome.EXPIRED_UNCONFIRMED)]
    )

    assert both.conversion_percent == 75
    assert small.conversion_percent is None


def test_closure_changes_say_which_way_they_went() -> None:
    cells = [
        _cell("U10", count=25, reason_code="suspended"),
        _cell("U10", count=11, reason_code="resumed"),
    ]

    counts = {r.code: r.count for r in _report(cells).closure.rows}

    assert counts == {"suspended": 25, "resumed": 11}


def test_bookings_from_before_reporting_are_a_table_of_their_own() -> None:
    old = [
        DayStatusCount(1, date(2026, 11, 3), BookingStatus.CANCELLED, 9),
        DayStatusCount(1, date(2026, 11, 4), BookingStatus.CANCELLED, 4),
        DayStatusCount(1, date(2026, 10, 1), BookingStatus.CANCELLED, 50),
        DayStatusCount(9, date(2026, 11, 4), BookingStatus.CANCELLED, 50),
    ]

    table = _report([], before_reporting=old).before_reporting

    counts = {r.code: r.count for r in table.rows}
    assert counts["cancelled"] == 13
    assert table.total == 13


def test_a_threshold_that_an_organisation_raised_hides_more() -> None:
    cells = [_cell("U2", count=8, outcome=UsageOutcome.CANCELLED_BY_PLANNER), _cell("U1", count=40)]

    report = volume_report(cells, [GROUP], FIRST, LAST, threshold=10, now=NOW, as_of=None)

    assert {r.code: r.count for r in report.outcomes.rows}["cancelled_by_planner"] is None
    assert report.as_of is None


def test_no_row_of_any_table_names_a_person_or_a_booking() -> None:
    cells = [_cell("U1"), _cell("U2", outcome=UsageOutcome.COMPLETED)]
    report = _report(cells)
    text = repr(report)

    for forbidden in ("name", "email", "booking_id", "token", "phone"):
        assert forbidden not in text.replace("organisation_name", "")
