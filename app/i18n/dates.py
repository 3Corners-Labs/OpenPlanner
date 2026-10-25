"""Locale-aware date and time formatting (Babel), in the group's time zone.

Times are stored in UTC; this is the edge where they become local text (CLAUDE.md).
"""

from datetime import date, datetime, tzinfo

from babel import Locale
from babel.dates import format_date, format_time, get_day_names


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
    """Return the local time of ``moment`` in ``tz``, e.g. ``09:00`` / ``9:00 AM``."""
    return format_time(moment.astimezone(tz), format="short", locale=_babel_locale(locale))


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
