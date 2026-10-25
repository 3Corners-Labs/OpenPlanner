"""The slot filter ("Wanneer past het u?") and the month layout, without HTTP."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.api.slot_picker import SlotFilter, month_weeks, parse_filter
from app.domain.availability import AvailableSlot

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _slot(local: datetime, minutes: int = 30) -> AvailableSlot:
    start = local.replace(tzinfo=AMSTERDAM).astimezone(UTC)
    return AvailableSlot(start, start + timedelta(minutes=minutes), 1, 1)


FRIDAY_1700 = _slot(datetime(2026, 1, 2, 17, 0))


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ([], SlotFilter()),
        ([("days", "4"), ("from", "17")], SlotFilter(frozenset({4}), 17)),
        ([("days", "4"), ("days", "4"), ("days", "0")], SlotFilter(frozenset({0, 4}))),
        ([("days", "7"), ("days", ""), ("days", "45"), ("from", "-1")], SlotFilter()),
        ([("from", "24"), ("until", "0")], SlotFilter()),
        ([("until", "24")], SlotFilter(end_hour=24)),
        ([("from", "15"), ("until", "15")], SlotFilter(start_hour=15, invalid_range=True)),
    ],
)
def test_parse_filter(params: list[tuple[str, str]], expected: SlotFilter) -> None:
    assert parse_filter(params) == expected


def test_friday_after_five() -> None:
    fits = SlotFilter(frozenset({4}), start_hour=17)

    assert fits.matches(FRIDAY_1700, AMSTERDAM)
    assert not fits.matches(_slot(datetime(2026, 1, 2, 16, 30)), AMSTERDAM)
    assert not fits.matches(_slot(datetime(2026, 1, 1, 17, 0)), AMSTERDAM)  # a Thursday


def test_until_means_finished_by() -> None:
    assert SlotFilter(end_hour=18).matches(_slot(datetime(2026, 1, 2, 17, 30)), AMSTERDAM)
    assert not SlotFilter(end_hour=18).matches(_slot(datetime(2026, 1, 2, 17, 45)), AMSTERDAM)
    assert SlotFilter(end_hour=24).matches(_slot(datetime(2026, 1, 2, 23, 30)), AMSTERDAM)


def test_query_round_trips() -> None:
    chosen = SlotFilter(frozenset({4, 1}), 9, 12)
    assert chosen.query() == [("days", "1"), ("days", "4"), ("from", "9"), ("until", "12")]
    assert parse_filter(chosen.query()) == chosen
    assert not SlotFilter().active


def test_month_weeks_start_on_monday() -> None:
    weeks = month_weeks(date(2026, 10, 1))  # a Thursday
    assert weeks[0][:3] == [None, None, None]
    assert weeks[0][3] == date(2026, 10, 1)
    assert all(len(week) == 7 for week in weeks)
    assert weeks[-1][5] == date(2026, 10, 31)
