from datetime import date

import pytest

from app.domain.errors import DomainError
from app.domain.holidays import (
    GroupHolidays,
    Holiday,
    HolidayCalendar,
    HolidaySource,
    closures,
    dutch_national_holidays,
    easter_sunday,
    with_holidays,
)
from app.domain.scheduling import ScheduleOverride
from tests.support.domain import window

NATIONAL, REGIONAL = 1, 2


@pytest.mark.parametrize(
    ("year", "easter"),
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),  # latest possible date
        (2285, date(2285, 3, 22)),  # earliest possible date
    ],
)
def test_easter_sunday(year: int, easter: date) -> None:
    assert easter_sunday(year) == easter


def test_dutch_national_holidays_2026() -> None:
    assert dutch_national_holidays(2026) == [
        (date(2026, 1, 1), "holiday.nl.new_year"),
        (date(2026, 4, 5), "holiday.nl.easter_sunday"),
        (date(2026, 4, 6), "holiday.nl.easter_monday"),
        (date(2026, 4, 27), "holiday.nl.kings_day"),
        (date(2026, 5, 5), "holiday.nl.liberation_day"),
        (date(2026, 5, 14), "holiday.nl.ascension_day"),
        (date(2026, 5, 24), "holiday.nl.whit_sunday"),
        (date(2026, 5, 25), "holiday.nl.whit_monday"),
        (date(2026, 12, 25), "holiday.nl.christmas_day"),
        (date(2026, 12, 26), "holiday.nl.boxing_day"),
    ]


def test_kings_day_moves_to_saturday_when_27_april_is_a_sunday() -> None:
    days = dict((key, day) for day, key in dutch_national_holidays(2025))

    assert days["holiday.nl.kings_day"] == date(2025, 4, 26)


@pytest.mark.parametrize("year", [1999, 2101])
def test_generator_refuses_implausible_years(year: int) -> None:
    with pytest.raises(DomainError) as caught:
        dutch_national_holidays(year)

    assert caught.value.field == "year"


@pytest.mark.parametrize("year", [2000, 2100])
def test_generator_accepts_the_bounds(year: int) -> None:
    assert len(dutch_national_holidays(year)) == 10


@pytest.mark.parametrize(
    ("slug", "name", "field"),
    [("Nationaal", "Nationale feestdagen", "slug"), ("nl", " ", "name"), ("nl", "a\nb", "name")],
)
def test_calendar_validates_slug_and_name(slug: str, name: str, field: str) -> None:
    with pytest.raises(DomainError) as caught:
        HolidayCalendar(1, slug, name)

    assert caught.value.field == field


def test_holiday_validates_its_name() -> None:
    with pytest.raises(DomainError) as caught:
        Holiday(NATIONAL, date(2026, 1, 1), "")

    assert caught.value.field == "name"


def test_holiday_id_takes_no_part_in_equality() -> None:
    assert Holiday(NATIONAL, date(2026, 1, 1), "Nieuwjaarsdag", id=4) == Holiday(
        NATIONAL, date(2026, 1, 1), "Nieuwjaarsdag"
    )


def test_a_group_inherits_the_organisation_calendars_by_default() -> None:
    assert GroupHolidays().calendars(frozenset({NATIONAL})) == {NATIONAL}


def test_a_group_with_its_own_choice_ignores_the_organisation() -> None:
    own = GroupHolidays(HolidaySource.OWN, frozenset({REGIONAL}))
    none = GroupHolidays(HolidaySource.OWN, frozenset())

    assert own.calendars(frozenset({NATIONAL})) == {REGIONAL}
    assert none.calendars(frozenset({NATIONAL})) == frozenset()


def test_closures_give_one_holiday_per_date_and_skip_days_kept_open() -> None:
    new_year = date(2026, 1, 1)
    kings_day = date(2026, 4, 27)
    holidays = [
        Holiday(REGIONAL, new_year, "Regionaal"),
        Holiday(NATIONAL, kings_day, "Koningsdag"),
        Holiday(NATIONAL, new_year, "Nieuwjaarsdag"),
    ]

    assert closures(holidays, ()) == [
        Holiday(NATIONAL, new_year, "Nieuwjaarsdag"),
        Holiday(NATIONAL, kings_day, "Koningsdag"),
    ]
    assert closures(holidays, [new_year]) == [Holiday(NATIONAL, kings_day, "Koningsdag")]


def test_with_holidays_closes_holidays_but_the_group_exception_wins() -> None:
    own_hours = ScheduleOverride(date(2026, 5, 5), window("10:00", "12:00", 1))
    holidays = [
        Holiday(NATIONAL, date(2026, 4, 27), "Koningsdag"),
        Holiday(NATIONAL, date(2026, 5, 5), "Bevrijdingsdag"),
    ]

    assert with_holidays([own_hours], holidays) == [
        own_hours,
        ScheduleOverride(date(2026, 4, 27), None),
    ]
