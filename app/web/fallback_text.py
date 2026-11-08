"""The messages a team space gets during a fallback (ADR 0082 point 12).

A fixed layout with i18n text blocks in the organisation's default locale (ADR 0009): on
activation the group's name and a link to its queue; per booking the start time, the first
name and the same link. Never the host join link: claiming in the queue gives that (ADR 0043
point 6), so the space keeps no link that outlives the booking.
"""

import re
from collections.abc import Callable

from app.domain.booking import Booking
from app.domain.scheduling import Group
from app.i18n.dates import format_clock, format_day

MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+\-.!|~@:])")
"""Characters with a meaning in Webex markdown (links, mentions, emphasis, headings)."""


def escape_markdown(text: str) -> str:
    """Return ``text`` with every markdown character escaped, so it shows as typed.

    A participant's first name or an admin's group name must never become a link, a mention
    or formatting in a message the organisation's bot posts.
    """
    return MARKDOWN_SPECIAL.sub(r"\\\1", text)


class SpaceTexts:
    """``FallbackTexts`` with the translator.

    Args:
        gettext: ``Translator.gettext`` (locale first).
        base_url: The portal's public address, for the queue link.
    """

    def __init__(self, gettext: Callable[..., str], base_url: str) -> None:
        """Store the collaborators."""
        self._gettext = gettext
        self._base = base_url.rstrip("/")

    def _link(self, group: Group) -> str:
        """Return the group's queue in the portal."""
        return f"{self._base}/admin/groups/{group.slug}/queue"

    def activated(self, group: Group, locale: str) -> str:
        """Return the activation message, with a link to the queue."""
        return self._gettext(
            locale,
            "fallback.space.activated",
            group=escape_markdown(group.name),
            link=self._link(group),
        )

    def queued(self, group: Group, booking: Booking, locale: str) -> str:
        """Return one booking's line: start time, first name, link (never the join link)."""
        when = (
            f"{format_day(booking.start.astimezone(group.tz).date(), locale)} "
            f"{format_clock(booking.start, group.tz, locale)}"
        )
        first_name = booking.contact.first_name if booking.contact else "–"
        return self._gettext(
            locale,
            "fallback.space.queued",
            time=when,
            first_name=escape_markdown(first_name),
            link=self._link(group),
        )
