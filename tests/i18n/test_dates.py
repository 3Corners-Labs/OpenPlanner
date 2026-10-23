from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.i18n.dates import (
    format_clock,
    format_day,
    format_day_compact,
    format_day_short,
    local_day,
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
WINTER = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # 09:00 CET
SUMMER = datetime(2026, 6, 1, 7, 30, tzinfo=UTC)  # 09:30 CEST


def test_full_day_per_locale() -> None:
    assert format_day(date(2026, 1, 5), "nl") == "maandag 5 januari 2026"
    assert format_day(date(2026, 1, 5), "en") == "Monday, January 5, 2026"


def test_short_day_has_no_year() -> None:
    assert format_day_short(date(2026, 1, 5), "nl") == "maandag 5 januari"
    assert format_day_short(date(2026, 1, 5), "en") == "Monday 5 January"


def test_compact_day_for_tables() -> None:
    assert format_day_compact(date(2026, 1, 5), "nl") == "5 jan 2026"
    assert format_day_compact(date(2026, 1, 5), "en") == "Jan 5, 2026"


def test_clock_uses_the_group_time_zone_and_locale_conventions() -> None:
    assert format_clock(WINTER, AMSTERDAM, "nl") == "09:00"
    assert format_clock(SUMMER, AMSTERDAM, "nl") == "09:30"
    assert format_clock(WINTER, AMSTERDAM, "en").replace(" ", " ") == "9:00 AM"


def test_regional_locale_codes_are_accepted() -> None:
    assert format_day(date(2026, 1, 5), "en-gb") == "Monday, 5 January 2026"


def test_local_day_crosses_midnight() -> None:
    late = datetime(2026, 1, 5, 23, 30, tzinfo=UTC)  # 00:30 on 6 January in Amsterdam

    assert local_day(late, AMSTERDAM) == date(2026, 1, 6)
    assert local_day(late, UTC) == date(2026, 1, 5)
