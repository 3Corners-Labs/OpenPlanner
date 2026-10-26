"""Holiday calendars: closed days configured once by the superuser, reused everywhere (ADR 0022).

A ``HolidayCalendar`` (for example "Nationale feestdagen") holds ``Holiday`` dates. Who uses
which calendar is decided at two levels, and the most specific choice wins:

    superuser      maintains the calendars and their dates (holidays.manage)
    organisation   chooses the calendars its groups follow (holidays.assign)
    group          inherits the organisation's choice, or picks its own (possibly none);
                   may keep single holidays open as usual (schedule.manage)

On one date the group's opening hours resolve in this order:

    group exception (ScheduleOverride) on that date   → its windows, or closed
    holiday of an effective calendar, not kept open    → closed
    otherwise                                          → the weekly rules

``with_holidays`` turns the holidays into closed overrides for the dates without an exception
of their own, so ``scheduling.windows_on`` and everything after it stay unchanged.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import ScheduleOverride, check_name, check_name_and_slug

MIN_YEAR = 2000
MAX_YEAR = 2100
"""Years the national-holiday generator accepts; protects against typos such as 20026."""


def _invalid(field_name: str) -> DomainError:
    """Return the error for an invalid holiday ``field_name``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field_name)


@dataclass(frozen=True)
class HolidayCalendar:
    """A reusable set of closed days, maintained by the superuser.

    Raises:
        DomainError: ``invalid_configuration`` with field ``slug`` or ``name``.
    """

    id: int
    slug: str
    name: str

    def __post_init__(self) -> None:
        """Validate slug and name."""
        check_name_and_slug(self.name, self.slug)


@dataclass(frozen=True)
class Holiday:
    """One closed date of a calendar, with its name as the superuser entered it.

    ``id`` identifies a stored holiday (for removal); it takes no part in equality.

    Raises:
        DomainError: ``invalid_configuration`` with field ``name``.
    """

    calendar_id: int
    day: date
    name: str
    id: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        """Validate the name."""
        check_name(self.name)


class HolidaySource(StrEnum):
    """Where a group's holiday calendars come from."""

    INHERIT = "inherit"
    OWN = "own"


@dataclass(frozen=True)
class GroupHolidays:
    """A group's holiday choice.

    Attributes:
        source: ``inherit`` follows the organisation; ``own`` uses ``calendar_ids``
            (an empty set means the group observes no holidays).
        calendar_ids: The group's own calendars; kept while inheriting, so switching back
            and forth loses nothing.
        open_days: Holidays on which the group is open as usual (its weekly rules apply).
    """

    source: HolidaySource = HolidaySource.INHERIT
    calendar_ids: frozenset[int] = frozenset()
    open_days: frozenset[date] = frozenset()

    def calendars(self, organisation_calendar_ids: frozenset[int]) -> frozenset[int]:
        """Return the calendars that apply to the group."""
        if self.source is HolidaySource.INHERIT:
            return organisation_calendar_ids
        return self.calendar_ids


def closures(holidays: Iterable[Holiday], open_days: Iterable[date]) -> list[Holiday]:
    """Return one holiday per closed date, sorted by date; ``open_days`` are left out.

    When two calendars close the same date, the first one by calendar id names it.
    """
    kept_open = set(open_days)
    by_day: dict[date, Holiday] = {}
    for holiday in sorted(holidays, key=lambda h: (h.day, h.calendar_id)):
        if holiday.day not in kept_open:
            by_day.setdefault(holiday.day, holiday)
    return list(by_day.values())


def with_holidays(
    overrides: Sequence[ScheduleOverride], holidays: Iterable[Holiday]
) -> list[ScheduleOverride]:
    """Return ``overrides`` plus a closed override for every holiday without an exception.

    A group's own exception on a holiday wins: it may open the day with its own hours.
    """
    excepted = {o.day for o in overrides}
    added = [ScheduleOverride(h.day, None) for h in holidays if h.day not in excepted]
    return [*overrides, *added]


def easter_sunday(year: int) -> date:
    """Return Easter Sunday of ``year`` (Gregorian calendar, anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def dutch_national_holidays(year: int) -> list[tuple[date, str]]:
    """Return the Dutch national holidays of ``year`` as (date, catalogue key), by date.

    The days of the Algemene termijnenwet plus both Easter and Whit Sundays and Bevrijdingsdag
    every year (as in the CAO Rijk). Koningsdag moves to 26 April when 27 April is a Sunday.
    Goede Vrijdag is not included; the superuser adds it to a calendar where it applies.

    Raises:
        DomainError: ``invalid_configuration`` with field ``year`` outside
            ``MIN_YEAR``-``MAX_YEAR``.
    """
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise _invalid("year")
    easter = easter_sunday(year)
    kings_day = date(year, 4, 27)
    if kings_day.weekday() == 6:
        kings_day = date(year, 4, 26)
    days = [
        (date(year, 1, 1), "holiday.nl.new_year"),
        (easter, "holiday.nl.easter_sunday"),
        (easter + timedelta(days=1), "holiday.nl.easter_monday"),
        (kings_day, "holiday.nl.kings_day"),
        (date(year, 5, 5), "holiday.nl.liberation_day"),
        (easter + timedelta(days=39), "holiday.nl.ascension_day"),
        (easter + timedelta(days=49), "holiday.nl.whit_sunday"),
        (easter + timedelta(days=50), "holiday.nl.whit_monday"),
        (date(year, 12, 25), "holiday.nl.christmas_day"),
        (date(year, 12, 26), "holiday.nl.boxing_day"),
    ]
    return sorted(days)
