"""What the staffing pages of a counted pool show of its shortfalls and its time zone (ADR 0122).

View models and the time-zone field of the pages in ``app.admin.pool_staffing_routes``. The
blocks and exceptions themselves are shown by ``app.admin.staffing_views``, which a service's
staffing tab uses as well.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.admin.forms import Field, FormData
from app.admin.timezones import DEFAULT_TIMEZONE, timezone_options
from app.domain.pool_accounting import Shortfall
from app.i18n.dates import format_clock, format_day_short
from app.web.locale import RequestLocale

TIMEZONE_FIELD = "timezone"
"""The name and id of the time-zone select, where an error about it links to."""


@dataclass(frozen=True)
class ShortfallRow:
    """One stretch where more places are held than people are counted, as a sentence."""

    text: str


@dataclass(frozen=True)
class ShortfallList:
    """The shortfalls to show: the first ones as sentences and how many more there are."""

    rows: tuple[ShortfallRow, ...]
    more: int


def _moment(moment: datetime, zone: ZoneInfo, locale: str) -> str:
    """Return the day and clock time of ``moment`` in the pool's zone, e.g. ``12 nov 10:00``."""
    day = format_day_short(moment.astimezone(zone).date(), locale)
    return f"{day} {format_clock(moment, zone, locale)}"


def _when(item: Shortfall, zone: ZoneInfo, locale: str) -> str:
    """Return a shortfall's stretch: ``12 nov 10:00–10:30``, or both days when it spans two."""
    start = _moment(item.start, zone, locale)
    if item.start.astimezone(zone).date() == item.end.astimezone(zone).date():
        return f"{start}–{format_clock(item.end, zone, locale)}"
    return f"{start} – {_moment(item.end, zone, locale)}"


def shortfall_list(
    found: tuple[Shortfall, ...], total: int, zone_name: str, locale: RequestLocale
) -> ShortfallList:
    """Turn the pool's listed shortfalls into sentences in the pool's time zone.

    ``total`` is how many there are in all; the page model carries only the first ones.
    """
    _ = locale.gettext
    zone = ZoneInfo(zone_name)
    rows = [
        ShortfallRow(
            _(
                "admin.pool_staffing.shortfall_row",
                when=_when(item, zone, locale.code),
                units=item.units,
                load=item.load,
            )
        )
        for item in found
    ]
    return ShortfallList(tuple(rows), max(0, total - len(found)))


def timezone_field(form: FormData, locale: RequestLocale, stored: str | None) -> Field:
    """Return the time-zone select: the submitted zone, else the stored one, else the default."""
    _ = locale.gettext
    zone = form.text(TIMEZONE_FIELD) or stored or DEFAULT_TIMEZONE
    return Field(
        TIMEZONE_FIELD,
        _("admin.field.timezone"),
        "select",
        zone,
        hint=_("admin.pool_staffing.timezone_hint"),
        error=form.error_for(TIMEZONE_FIELD),
        options=timezone_options(zone),
    )
