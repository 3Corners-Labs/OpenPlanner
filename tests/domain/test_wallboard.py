"""The figures of the live wallboard: live counts exact, rollup figures through the small cell."""

from datetime import UTC, datetime, timedelta

from app.domain.usage import UsageCell, UsageOutcome
from app.domain.usage_report import ReportGroup
from app.domain.wallboard import (
    BlockFill,
    LiveQueue,
    fill_percent,
    live_figures,
    over_alert,
    wallboard,
)

NOW = datetime(2026, 11, 9, 10, 20, tzinfo=UTC)  # Amsterdam: 11:20, so the local hour is 11
GROUP = ReportGroup(1, 1, "demo", "Demo", "acme", "Acme", "Europe/Amsterdam")
THRESHOLD = 5


def _cell(metric: str, hour: int, count: int, **cut: object) -> UsageCell:
    return UsageCell(
        datetime(2026, 11, 9, hour, tzinfo=UTC),
        1,
        1,
        metric,
        count,
        count,
        count,
        **cut,  # type: ignore[arg-type]
    )


def _board(cells: list[UsageCell], live: list[LiveQueue] | None = None, **more: object):  # type: ignore[no-untyped-def]
    return wallboard(
        [GROUP],
        cells,
        live or [],
        more.get("now_fill"),  # type: ignore[arg-type]
        more.get("next_fill"),  # type: ignore[arg-type]
        NOW,
        THRESHOLD,
        5,
        NOW - timedelta(minutes=3),
    )


def test_the_live_counts_are_added_over_the_groups_and_exact() -> None:
    figures = live_figures(
        [LiveQueue(1, timedelta(seconds=90), 2), LiveQueue(2, timedelta(minutes=7), 0)], 3
    )

    assert figures is not None
    assert (figures.waiting, figures.longest_wait_seconds, figures.connected) == (3, 420, 2)
    assert (figures.groups, figures.of_groups) == (2, 3)  # one group has no own queue


def test_no_own_queue_means_no_live_figures() -> None:
    assert live_figures([], 2) is None
    assert _board([]).live is None


def test_a_wait_beyond_the_limit_is_a_long_wait() -> None:
    figures = live_figures([LiveQueue(1, timedelta(minutes=6), 0)], 1)

    assert over_alert(figures, 5) is True
    assert over_alert(figures, 6) is False  # at the limit is not beyond it
    assert over_alert(None, 5) is False


def test_the_appointments_today_are_the_bookings_of_the_local_day_and_hidden_when_small() -> None:
    board = _board([_cell("C4", 7, 3), _cell("C4", 8, 4), _cell("C4", 9, 6)])

    assert board.appointments == 13
    assert _board([_cell("C4", 9, 4)]).appointments is None  # below the threshold of 5


def test_a_cell_of_another_day_or_group_is_not_counted() -> None:
    yesterday = UsageCell(datetime(2026, 11, 8, 9, tzinfo=UTC), 1, 1, "C4", 9, 9, 9)
    other = UsageCell(datetime(2026, 11, 9, 9, tzinfo=UTC), 1, 2, "C4", 9, 9, 9)

    # nothing of today is left, and a count below the threshold (zero too) reads < n
    assert _board([yesterday, other]).appointments is None
    assert _board([yesterday, other, _cell("C4", 9, 6)]).appointments == 6


def test_no_shows_so_far_come_from_the_outcome_cells() -> None:
    board = _board(
        [
            _cell("U2", 8, 6, outcome=UsageOutcome.NO_SHOW),
            _cell("U2", 8, 9, outcome=UsageOutcome.COMPLETED),
        ]
    )

    assert board.no_shows == 6


def test_failed_hand_overs_cover_this_hour_and_the_one_before_only() -> None:
    cells = [_cell("Q8", 8, 99), _cell("Q8", 9, 3), _cell("Q8", 10, 4)]  # 08:00 UTC is too old

    assert _board(cells).failed == 7


def test_the_hour_strip_shades_what_has_not_started() -> None:
    board = _board([_cell("C4", 7, 6), _cell("C4", 10, 8), _cell("C4", 13, 9)])

    by_hour = {b.hour: b for b in board.bars}
    assert by_hour[8].planned is False  # 07:00 UTC is 08:00 local
    assert by_hour[11].current
    assert not by_hour[11].planned
    assert by_hour[14].planned  # the part of the day still to come
    assert by_hour[11].count == 8


def test_the_hour_strip_always_shows_the_default_hours_and_hides_small_hours() -> None:
    board = _board([_cell("C4", 10, 2)])

    hours = [b.hour for b in board.bars]
    assert hours[0] <= 8
    assert hours[-1] >= 16
    assert next(b for b in board.bars if b.hour == 11).count is None  # 2 < threshold


def test_a_block_fill_is_a_percentage_only_when_its_bookings_may_be_shown() -> None:
    assert fill_percent(BlockFill(8, 10), THRESHOLD) == 80
    assert fill_percent(BlockFill(3, 10), THRESHOLD) is None  # 30 % would give 3 people back
    assert fill_percent(BlockFill(0, 10), THRESHOLD) == 0
    assert fill_percent(BlockFill(2, 0), THRESHOLD) is None
    assert fill_percent(None, THRESHOLD) is None
    assert fill_percent(BlockFill(12, 10), THRESHOLD) == 100  # a held-over place does not go past


def test_the_board_says_which_rollup_its_historic_figures_come_from() -> None:
    assert _board([]).rolled_at == NOW - timedelta(minutes=3)
