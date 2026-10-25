"""The time-zone choices of the group forms.

An admin picks an IANA zone from a select instead of typing one. Only the zone name is stored;
summer and winter time follow from the zone itself, so a zone such as ``Europe/Amsterdam``
switches between CET and CEST without any change by the admin. Fixed-offset zones
(``Etc/GMT+1``) and legacy aliases (``CET``, ``GB``) are not offered: the former ignore
daylight saving time, the latter are ambiguous.
"""

from functools import cache
from zoneinfo import available_timezones

from app.admin.forms import Option

DEFAULT_TIMEZONE = "Europe/Amsterdam"
"""Preselected for a new group and listed first."""

_REGIONS = (
    "Africa",
    "America",
    "Antarctica",
    "Asia",
    "Atlantic",
    "Australia",
    "Europe",
    "Indian",
    "Pacific",
)


@cache
def _region_zones() -> tuple[str, ...]:
    """Return the ``Region/City`` zones of the tz database, sorted, the default first."""
    zones = sorted(z for z in available_timezones() if z.split("/", 1)[0] in _REGIONS)
    rest = [z for z in zones if z != DEFAULT_TIMEZONE]
    return (DEFAULT_TIMEZONE, *rest, "UTC")


def timezone_options(current: str) -> tuple[Option, ...]:
    """Return the select options; a stored zone outside the list stays selectable.

    Args:
        current: The zone the group has now (or the submitted value), kept as an option so
            that saving another setting never changes the zone unnoticed. A value that is no
            zone of the tz database (a tampered form) is not echoed back.
    """
    zones = _region_zones()
    if current not in zones and current in available_timezones():
        zones = (current, *zones)
    return tuple(Option(zone, zone.replace("_", " ")) for zone in zones)
