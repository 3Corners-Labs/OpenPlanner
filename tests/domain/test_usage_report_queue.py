"""The Queue and calls report: tables from hourly cells, small cells hidden (ADR 0117 step 6)."""

from datetime import UTC, date, datetime

from app.domain.booking import BookingKind
from app.domain.channels import ChannelKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import ActorClass, UsageCell, duration_bucket
from app.domain.usage_report import (
    SIGNED_BUCKETS,
    UNSIGNED_BUCKETS,
    ReportGroup,
    distribution,
)
from app.domain.usage_report_queue import queue_report

GROUP = ReportGroup(1, 1, "balie", "Balie", "demo", "Demo", "Europe/Amsterdam")
FIRST = date(2026, 11, 2)
LAST = date(2026, 11, 8)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
HOUR = datetime(2026, 11, 4, 10, tzinfo=UTC)


def _cell(metric: str, *, count: int = 10, total: int = 0, hour: datetime = HOUR, **cuts: object):  # type: ignore[no-untyped-def]
    return UsageCell(hour, 1, 1, metric, count, total, total, **cuts)  # type: ignore[arg-type]


def _report(cells: list[UsageCell], threshold: int = 5):  # type: ignore[no-untyped-def]
    return queue_report(cells, [GROUP], FIRST, LAST, threshold=threshold, as_of=NOW)


def test_without_any_cell_the_report_is_empty_and_every_table_is_hidden_or_zero() -> None:
    report = _report([])

    assert report.empty
    assert report.calls_ended is None
    assert report.peak is None
    assert report.task_wait.mean_seconds is None
    assert report.rebook_conversion_percent is None


def test_cells_of_other_screens_do_not_make_this_one_non_empty() -> None:
    assert _report([_cell("U1"), _cell("C1")]).empty


def test_the_queue_mix_is_a_grid_of_method_and_kind() -> None:
    cells = [
        _cell(
            "Q1",
            count=12,
            dispatch_kind=DispatchKind.CONTACT_CENTER,
            booking_kind=BookingKind.SCHEDULED,
        ),
        _cell(
            "Q1",
            count=9,
            dispatch_kind=DispatchKind.CONTACT_CENTER,
            booking_kind=BookingKind.DIRECT,
        ),
        _cell(
            "Q1",
            count=20,
            dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
            booking_kind=BookingKind.SCHEDULED,
        ),
        _cell(
            "Q1",
            count=25,
            dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
            booking_kind=BookingKind.DIRECT,
        ),
    ]

    mix = _report(cells).mix

    assert mix.rows == ("contact_center", "openplanner_queue")
    assert mix.columns == ("scheduled", "direct")
    assert mix.cells == ((12, 9), (20, 25))
    assert mix.total == 66


def test_a_small_mix_cell_is_hidden_with_a_neighbour() -> None:
    cells = [
        _cell(
            "Q1",
            count=2,
            dispatch_kind=DispatchKind.CONTACT_CENTER,
            booking_kind=BookingKind.DIRECT,
        ),
        _cell(
            "Q1",
            count=30,
            dispatch_kind=DispatchKind.CONTACT_CENTER,
            booking_kind=BookingKind.SCHEDULED,
        ),
        _cell(
            "Q1",
            count=40,
            dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
            booking_kind=BookingKind.SCHEDULED,
        ),
        _cell(
            "Q1",
            count=50,
            dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
            booking_kind=BookingKind.DIRECT,
        ),
    ]

    mix = _report(cells).mix

    assert mix.cells[0][1] is None
    assert sum(v is None for row in mix.cells for v in row) >= 2


def test_task_wait_is_counted_in_buckets_with_an_exact_mean() -> None:
    cells = [
        _cell("Q2", count=10, total=1500, bucket=duration_bucket(150)),
        _cell("Q2", count=10, total=3500, bucket=duration_bucket(350)),
    ]

    wait = _report(cells).task_wait

    assert [r.count for r in wait.table.rows][:3] == [None, 10, 10]
    assert wait.table.total == 20
    assert wait.mean_seconds == 250


def test_a_mean_is_not_shown_when_its_total_is_a_small_cell() -> None:
    wait = _report([_cell("Q2", count=3, total=600, bucket=1)]).task_wait

    assert wait.table.total is None
    assert wait.mean_seconds is None


def test_punctuality_uses_the_mirrored_buckets_and_leaves_empty_ones_out() -> None:
    cells = [
        _cell("Q3", count=8, total=-1200, bucket=duration_bucket(-150, signed=True)),
        _cell("Q3", count=12, total=1800, bucket=duration_bucket(150, signed=True)),
    ]

    punctuality = _report(cells).punctuality

    assert [r.code for r in punctuality.table.rows] == ["-2", "1"]
    assert punctuality.mean_seconds == round(600 / 20)


def test_distribution_with_no_cells_has_a_row_per_bucket_and_no_mean() -> None:
    d = distribution([], UNSIGNED_BUCKETS, 5)

    assert len(d.table.rows) == 10
    assert d.mean_seconds is None
    assert len(SIGNED_BUCKETS) == 20


def test_people_per_call_is_a_mean_in_tenths() -> None:
    cells = [_cell("U11", count=10, total=25, booking_kind=BookingKind.SCHEDULED)]

    report = _report(cells)

    assert report.calls_ended == 10
    assert report.participants_per_call_tenths == 25


def test_few_calls_show_no_mean_of_people() -> None:
    report = _report([_cell("U11", count=3, total=6)])

    assert report.calls_ended is None
    assert report.participants_per_call_tenths is None


def test_abandoned_direct_calls_are_a_share_of_those_started() -> None:
    cells = [
        _cell("Q6", count=40, reason_code="created", dispatch_kind=DispatchKind.CONTACT_CENTER),
        _cell("Q6", count=10, bucket=2, dispatch_kind=DispatchKind.CONTACT_CENTER),
        _cell("Q6", count=6, reason_code="not_picked_up", booking_kind=BookingKind.DIRECT),
    ]

    report = _report(cells)

    assert (report.direct_created, report.direct_left, report.direct_not_picked_up) == (40, 10, 6)
    assert report.abandoned_percent == 25
    assert report.abandon_wait.table.total == 10


def test_a_share_with_a_hidden_part_is_not_shown() -> None:
    cells = [
        _cell("Q6", count=40, reason_code="created"),
        _cell("Q6", count=2, bucket=1),
    ]

    report = _report(cells)

    assert report.direct_left is None
    assert report.abandoned_percent is None


def test_own_queue_claims_and_releases() -> None:
    cells = [
        _cell("Q7", count=20, total=6000, bucket=duration_bucket(300), reason_code="claimed"),
        _cell("Q7", count=10, reason_code="released"),
    ]

    report = _report(cells)

    assert (report.claimed, report.released) == (20, 10)
    assert report.releases_per_claim_tenths == 5
    assert report.claim_wait.mean_seconds == 300


def test_failures_have_a_row_for_each_known_code_and_for_new_ones() -> None:
    cells = [
        _cell("Q8", count=7, reason_code="task_failed.timeout"),
        _cell("Q8", count=9, reason_code="integration_error"),
    ]

    codes = [r.code for r in _report(cells).failures.rows]

    assert codes == ["task_failed", "meeting_failed", "integration_error", "task_failed.timeout"]


def test_the_peak_is_the_highest_of_the_day_and_hidden_for_few_calls() -> None:
    day2 = datetime(2026, 11, 5, 10, tzinfo=UTC)
    cells = [
        _cell("Q9", count=3, total=4),
        _cell("Q9", count=4, total=6, hour=datetime(2026, 11, 4, 15, tzinfo=UTC)),
        _cell("Q9", count=2, total=3, hour=day2),
    ]
    cells[0] = UsageCell(HOUR, 1, 1, "Q9", 3, 4, 4)
    cells[1] = UsageCell(datetime(2026, 11, 4, 15, tzinfo=UTC), 1, 1, "Q9", 4, 6, 6)
    cells[2] = UsageCell(day2, 1, 1, "Q9", 2, 3, 3)

    report = _report(cells)

    assert [(p.day, p.peak) for p in report.peaks] == [
        (date(2026, 11, 4), 6),
        (date(2026, 11, 5), None),
    ]
    assert report.peak == 6


def test_the_rebooking_funnel() -> None:
    cells = [
        _cell("U8", count=20, reason_code="proposed", actor_class=ActorClass.PLANNER),
        _cell("U8", count=10, reason_code="proposed", actor_class=ActorClass.SYSTEM),
        _cell("U8", count=15, reason_code="accepted", bucket=4),
        _cell("U8", count=9, reason_code="expired"),
    ]

    report = _report(cells)

    assert [r.count for r in report.rebook_proposed.rows] == [20, 10]
    assert report.rebook_proposed.total == 30
    assert (report.rebook_accepted, report.rebook_expired) == (15, 9)
    assert report.rebook_conversion_percent == 50
    assert report.rebook_wait.table.total == 15


def test_only_cells_of_the_groups_in_scope_and_range_count() -> None:
    cells = [
        _cell("Q5", count=20, total=2000, bucket=1),
        _cell("Q5", count=20, total=2000, bucket=1, hour=datetime(2026, 10, 1, 10, tzinfo=UTC)),
        UsageCell(HOUR, 1, 99, "Q5", 20, 2000, 2000, bucket=1),
    ]

    assert _report(cells).call_length.table.total == 20


def test_a_channel_kind_cell_does_not_confuse_the_queue_report() -> None:
    assert _report([_cell("M1", channel_kind=ChannelKind.EMAIL, reason_code="reminder.sent")]).empty
