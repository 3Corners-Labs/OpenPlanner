"""A report as CSV rows: the screen's tables with hidden cells, and the tidy file (ADR 0117)."""

from datetime import UTC, date, datetime

from app.domain.channels import ChannelKind
from app.domain.usage import UsageCell, UsageOutcome
from app.domain.usage_report import ReportGroup, volume_report
from app.domain.usage_report_capacity import capacity_report
from app.domain.usage_report_channels import channels_report
from app.domain.usage_report_csv import (
    HIDDEN,
    SCREEN_HEADER,
    TIDY_HEADER,
    screen_rows,
    tidy_rows,
)
from app.domain.usage_report_queue import queue_report

GROUP = ReportGroup(1, 1, "balie", "Balie", "demo", "Demo", "Europe/Amsterdam")
FIRST = date(2026, 11, 2)
LAST = date(2026, 11, 8)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
HOUR = datetime(2026, 11, 4, 10, tzinfo=UTC)


def _cell(metric: str, count: int, **cuts: object) -> UsageCell:
    return UsageCell(HOUR, 1, 1, metric, count, count * 60, 90, **cuts)  # type: ignore[arg-type]


def _volume(cells: list[UsageCell], threshold: int = 5):  # type: ignore[no-untyped-def]
    return volume_report(
        cells, [GROUP], FIRST, LAST, threshold=threshold, now=NOW, as_of=NOW, before_reporting=()
    )


def test_the_screen_file_has_the_days_and_the_outcomes_of_the_volume_report() -> None:
    rows = screen_rows(_volume([_cell("U1", 12), _cell("U2", 9, outcome=UsageOutcome.COMPLETED)]))

    assert len(SCREEN_HEADER) == len(rows[0])
    assert ("days", "2026-11-04", "count", 12, "") in rows
    assert ("outcomes", "completed", "count", 9, "") in rows
    assert ("created_total", "", "", 12, "") in rows


def test_a_small_cell_is_an_empty_value_with_the_note_hidden() -> None:
    rows = screen_rows(_volume([_cell("U1", 12), _cell("U2", 2, outcome=UsageOutcome.COMPLETED)]))

    hidden = [r for r in rows if r[:3] == ("outcomes", "completed", "count")]

    assert hidden == [("outcomes", "completed", "count", None, HIDDEN)]


def test_the_range_and_the_freshness_are_not_tables() -> None:
    names = {row[0] for row in screen_rows(_volume([_cell("U1", 12)]))}

    assert not names & {"first_day", "last_day", "as_of", "threshold", "empty", "provisional"}


def test_every_family_walks_to_rows_whatever_its_tables_look_like() -> None:
    cells = [_cell("U1", 12), _cell("Q1", 9), _cell("C1", 9), _cell("M1", 9)]
    reports = [
        queue_report(cells, [GROUP], FIRST, LAST, threshold=5, as_of=NOW),
        capacity_report(cells, [GROUP], FIRST, LAST, threshold=5, as_of=NOW),
        channels_report(cells, [GROUP], FIRST, LAST, threshold=5, as_of=NOW),
    ]

    for report in reports:
        rows = screen_rows(report)
        assert rows
        assert all(len(row) == len(SCREEN_HEADER) for row in rows)


def test_a_figure_is_hidden_exactly_when_the_screen_hides_it() -> None:
    rows = screen_rows(_volume([_cell("U1", 2)]))

    assert all(note == HIDDEN for _t, _r, _c, value, note in rows if value is None)
    assert all(value is None for _t, _r, _c, value, note in rows if note == HIDDEN)


def test_the_tidy_file_has_one_row_per_hourly_cell_and_leaves_small_cells_out() -> None:
    cells = [
        _cell("M1", 8, channel_kind=ChannelKind.EMAIL, outcome=UsageOutcome.COMPLETED),
        _cell("M1", 2, channel_kind=ChannelKind.SMS),
    ]

    tidy = tidy_rows(cells, [GROUP], FIRST, LAST, 5)

    assert len(tidy.rows) == 1
    assert tidy.hidden == 1
    row = dict(zip(TIDY_HEADER, tidy.rows[0], strict=True))
    assert row["hour"] == "2026-11-04T10:00:00Z"
    assert (row["organisation"], row["group"], row["metric"]) == ("demo", "balie", "M1")
    assert (row["channel"], row["count"], row["sum"], row["max"]) == ("email", 8, 480, 90)
    assert "sms" not in str(tidy.rows)


def test_the_tidy_file_covers_the_group_local_days_of_the_range_only() -> None:
    outside = UsageCell(datetime(2026, 11, 20, 10, tzinfo=UTC), 1, 1, "U1", 9, 0, 0)

    assert tidy_rows([outside], [GROUP], FIRST, LAST, 5).rows == []


def test_two_exports_of_the_same_cells_are_the_same_file() -> None:
    cells = [_cell("U1", 9), _cell("Q1", 9), _cell("C1", 9)]

    assert tidy_rows(cells, [GROUP], FIRST, LAST, 5) == tidy_rows(
        list(reversed(cells)), [GROUP], FIRST, LAST, 5
    )
