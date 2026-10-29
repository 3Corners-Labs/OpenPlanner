from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.i18n.dates import (
    ClockStyle,
    clock_scope,
    configure_clock,
    format_clock,
    format_day,
    format_day_compact,
    format_day_short,
    format_month,
    format_time_of_day,
    local_day,
    use_clock,
    weekday_abbreviations,
    weekday_names,
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
WINTER = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # 09:00 CET
SUMMER = datetime(2026, 6, 1, 7, 30, tzinfo=UTC)  # 09:30 CEST
AFTERNOON = datetime(2026, 1, 5, 14, 5, tzinfo=UTC)  # 15:05 CET


def test_full_day_per_locale() -> None:
    assert format_day(date(2026, 1, 5), "nl") == "maandag 5 januari 2026"
    assert format_day(date(2026, 1, 5), "en") == "Monday, January 5, 2026"


def test_short_day_has_no_year() -> None:
    assert format_day_short(date(2026, 1, 5), "nl") == "maandag 5 januari"
    assert format_day_short(date(2026, 1, 5), "en") == "Monday 5 January"


def test_compact_day_for_tables() -> None:
    assert format_day_compact(date(2026, 1, 5), "nl") == "5 jan 2026"
    assert format_day_compact(date(2026, 1, 5), "en") == "Jan 5, 2026"


@pytest.fixture
def clock_style() -> Iterator[None]:
    """Restore the 24-hour default after a test that changes the system clock style."""
    yield
    configure_clock(ClockStyle.H24)


def plain(text: str) -> str:
    """Replace Babel's (narrow) no-break space before AM/PM by a plain space."""
    return text.replace(" ", " ").replace("\xa0", " ")


def test_clock_uses_the_group_time_zone_in_24_hours_by_default() -> None:
    assert format_clock(WINTER, AMSTERDAM, "nl") == "09:00"
    assert format_clock(SUMMER, AMSTERDAM, "nl") == "09:30"
    assert format_clock(AFTERNOON, AMSTERDAM, "nl") == "15:05"
    assert format_clock(AFTERNOON, AMSTERDAM, "en") == "15:05"


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 3, 29, 0, 30, tzinfo=UTC), "01:30"),  # last half hour of CET
        (datetime(2026, 3, 29, 1, 0, tzinfo=UTC), "03:00"),  # first moment of CEST
        (datetime(2026, 10, 25, 0, 30, tzinfo=UTC), "02:30"),  # 02:30 CEST
        (datetime(2026, 10, 25, 1, 30, tzinfo=UTC), "02:30"),  # 02:30 CET, an hour later
        (datetime(2026, 10, 25, 2, 0, tzinfo=UTC), "03:00"),  # CET, UTC+1
    ],
)
def test_clock_follows_summer_and_winter_time(moment: datetime, expected: str) -> None:
    assert format_clock(moment, AMSTERDAM, "nl") == expected


@pytest.mark.usefixtures("clock_style")
def test_twelve_hour_clock_is_a_system_setting() -> None:
    configure_clock(ClockStyle.H12)

    assert plain(format_clock(AFTERNOON, AMSTERDAM, "en")) == "3:05 PM"
    assert plain(format_time_of_day(time(9, 0), "en")) == "9:00 AM"


@pytest.mark.usefixtures("clock_style")
def test_locale_clock_follows_each_language() -> None:
    configure_clock(ClockStyle.LOCALE)

    assert format_clock(AFTERNOON, AMSTERDAM, "nl") == "15:05"
    assert plain(format_clock(AFTERNOON, AMSTERDAM, "en")) == "3:05 PM"


def test_time_of_day_uses_the_clock_style() -> None:
    assert format_time_of_day(time(9, 0), "en") == "09:00"
    assert format_time_of_day(time(17, 30), "nl") == "17:30"


def test_regional_locale_codes_are_accepted() -> None:
    assert format_day(date(2026, 1, 5), "en-gb") == "Monday, 5 January 2026"


def test_local_day_crosses_midnight() -> None:
    late = datetime(2026, 1, 5, 23, 30, tzinfo=UTC)  # 00:30 on 6 January in Amsterdam

    assert local_day(late, AMSTERDAM) == date(2026, 1, 6)
    assert local_day(late, UTC) == date(2026, 1, 5)


def test_weekday_names_start_on_monday() -> None:
    assert weekday_names("nl")[0] == "maandag"
    assert weekday_names("nl")[6] == "zondag"
    assert weekday_names("en")[2] == "Wednesday"


def test_month_heading() -> None:
    assert format_month(date(2026, 10, 26), "nl") == "oktober 2026"
    assert format_month(date(2026, 10, 26), "en") == "October 2026"


def test_weekday_abbreviations_start_on_monday() -> None:
    assert weekday_abbreviations("nl") == ["ma", "di", "wo", "do", "vr", "za", "zo"]
    assert weekday_abbreviations("en")[0] == "Mo"


def test_an_organisation_clock_applies_inside_its_scope_only() -> None:
    nine = datetime(2026, 1, 5, 14, 5, tzinfo=UTC)
    zone = ZoneInfo("Europe/Amsterdam")

    with clock_scope():
        use_clock(ClockStyle.H12)
        inside = format_clock(nine, zone, "en")
    outside = format_clock(nine, zone, "en")

    assert inside == "3:05 PM" or inside == "3:05 PM"
    assert outside == "15:05"


def test_use_clock_outside_a_scope_changes_nothing() -> None:
    use_clock(ClockStyle.H12)

    assert format_clock(datetime(2026, 1, 5, 14, 5, tzinfo=UTC), ZoneInfo("UTC"), "en") == "14:05"
