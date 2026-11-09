"""What a digest mail is made of: the figures it may hold, and the finished mail (ADR 0117).

Plain data between the service that decides what goes out and the web-layer composer that writes
it in words (``app/web/digest_text.py``), so the domain holds no text and no markup.
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class DigestFigures:
    """The suppressed figures of the covered days; ``None`` is a figure that is hidden.

    Attributes:
        bookings: Bookings created.
        no_show_percent: No shows over completed plus no shows, in whole percent.
        average_wait_minutes: Mean wait until a call connects, in whole minutes.
        utilisation_percent: Booked over capacity, in whole percent.
    """

    bookings: int | None
    no_show_percent: int | None
    average_wait_minutes: int | None
    utilisation_percent: int | None


@dataclass(frozen=True)
class DigestContent:
    """What the composer is given for one mail.

    Attributes:
        organisation_slug: The organisation.
        organisation_id: Its id, for the organisation's own texts.
        group_slug: The group, or ``None`` for the whole organisation.
        group_id: Its id, or ``None``.
        first_day: First covered day.
        last_day: Last covered day.
        link: Where the report of those days is, in the portal; no token, no personal data.
        figures: The figures, or ``None`` when the organisation keeps them out of the mail.
    """

    organisation_slug: str
    organisation_id: int
    group_slug: str | None
    group_id: int | None
    first_day: date
    last_day: date
    link: str
    figures: DigestFigures | None = None


@dataclass(frozen=True)
class DigestMail:
    """A finished mail: subject, plain text and HTML; never an attachment."""

    subject: str = field(repr=False)
    text: str = field(repr=False)
    html: str = field(repr=False)
