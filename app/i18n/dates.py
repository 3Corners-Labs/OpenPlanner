"""Locale-aware date and time formatting (Babel), in the group's time zone.

Times are stored in UTC; this is the edge where they become local text (CLAUDE.md). Summer and
winter time follow from the IANA zone (``zoneinfo``): never add a fixed offset.

Clock times use one system-wide ``ClockStyle`` (``TIME_FORMAT``, default 24 hours), set once at
start-up with ``configure_clock``; dates always follow the locale.
"""

from datetime import date, datetime, time, tzinfo
from enum import StrEnum

from babel import Locale
from babel.dates import format_date, format_time, get_day_names


class ClockStyle(StrEnum):
    """How clock times are written, the same for every language.

    Attributes:
        H24: ``15:05`` (default; the convention in Dutch government communication).
        H12: ``3:05 PM``, with the locale's AM/PM marker.
        LOCALE: Babel's short time of the language: ``15:05`` in Dutch, ``3:05 PM`` in English.
    """

    H24 = "24h"
    H12 = "12h"
    LOCALE = "locale"


_CLOCK_PATTERNS: dict[ClockStyle, str] = {
    ClockStyle.H24: "HH:mm",
    ClockStyle.H12: "h:mm a",
    ClockStyle.LOCALE: "short",
}
_clock_pattern = _CLOCK_PATTERNS[ClockStyle.H24]


def configure_clock(style: ClockStyle) -> None:
    """Set the clock style for all later formatting (once per process, at start-up)."""
    global _clock_pattern  # one process-wide setting, read on every format
    _clock_pattern = _CLOCK_PATTERNS[style]


def _babel_locale(locale: str) -> Locale:
    """Return the Babel locale for a normalised code such as ``en-gb``."""
    return Locale.parse(locale.replace("-", "_"))


def format_day(day: date, locale: str) -> str:
    """Return a full date, e.g. ``maandag 5 januari 2026`` / ``Monday, January 5, 2026``."""
    return format_date(day, format="full", locale=_babel_locale(locale))


def format_day_short(day: date, locale: str) -> str:
    """Return a date without year for headings, e.g. ``maandag 5 januari``."""
    return format_date(day, format="EEEE d MMMM", locale=_babel_locale(locale))


def format_day_compact(day: date, locale: str) -> str:
    """Return a short date with year for tables, e.g. ``5 jan 2026`` / ``Jan 5, 2026``."""
    return format_date(day, format="medium", locale=_babel_locale(locale))


def format_clock(moment: datetime, tz: tzinfo, locale: str) -> str:
    """Return the local time of ``moment`` in ``tz`` in the clock style, e.g. ``09:00``."""
    return format_time(moment.astimezone(tz), format=_clock_pattern, locale=_babel_locale(locale))


def format_time_of_day(value: time, locale: str) -> str:
    """Return a wall-clock time without date (opening hours) in the clock style."""
    return format_time(value, format=_clock_pattern, locale=_babel_locale(locale))


def format_month(day: date, locale: str) -> str:
    """Return the month and year of ``day`` as a heading, e.g. ``oktober 2026``."""
    return format_date(day, format="LLLL y", locale=_babel_locale(locale))


def weekday_abbreviations(locale: str) -> list[str]:
    """Return short weekday names Monday first for calendar columns, e.g. ``ma`` … ``zo``."""
    names = get_day_names("short", locale=_babel_locale(locale))
    return [str(names[index]) for index in range(7)]


def weekday_names(locale: str) -> list[str]:
    """Return the weekday names Monday first, e.g. ``maandag`` … ``zondag``."""
    names = get_day_names("wide", locale=_babel_locale(locale))
    return [str(names[index]) for index in range(7)]


def local_day(moment: datetime, tz: tzinfo) -> date:
    """Return the local date of ``moment`` in ``tz``."""
    return moment.astimezone(tz).date()
