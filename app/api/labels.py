"""Labels shared by the participant pages: the appointment as people read it."""

from datetime import datetime

from app.domain.scheduling import Group
from app.i18n.dates import format_clock, format_day, local_day
from app.web.locale import RequestLocale


def slot_label(start: datetime, end: datetime, group: Group, locale: RequestLocale) -> str:
    """Return e.g. ``maandag 5 januari 2026, 09:00–09:30`` in the group's time zone."""
    day = format_day(local_day(start, group.tz), locale.code)
    return (
        f"{day}, {format_clock(start, group.tz, locale.code)}"
        f"–{format_clock(end, group.tz, locale.code)}"
    )
