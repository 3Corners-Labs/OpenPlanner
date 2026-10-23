"""How a booking's start time reads for agents on Agent Desktop (shared by web and worker)."""

from app.domain.booking import Booking
from app.domain.scheduling import Group
from app.i18n.dates import format_clock, format_day_compact, local_day

AGENT_LOCALE = "nl"
"""Language of the start time shown to agents."""


def agent_start_label(booking: Booking, group: Group) -> str:
    """Return e.g. ``5 jan 2026, 09:00`` in the group's time zone."""
    day = format_day_compact(local_day(booking.start, group.tz), AGENT_LOCALE)
    return f"{day}, {format_clock(booking.start, group.tz, AGENT_LOCALE)}"
