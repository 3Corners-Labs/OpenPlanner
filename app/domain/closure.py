"""Emergency closure of a group: no bookings, appointments on hold, citizens informed (ADR 0024).

A ``Closure`` covers the start times ``starts_at <= start < ends_at`` of one group; without
``ends_at`` it lasts until it is lifted. While it is visible (now before its end) the citizen
page shows its notice, and from its start no slot inside it is offered or booked.

Confirmed bookings inside the closure are suspended, not cancelled: they keep their personal
data and their place, the citizen gets the notice with a link to choose a new time, and an
admin can move, reinstate or cancel them. The personal data of a booking that is still
suspended is purged ``RETENTION_AFTER_CLOSURE`` after the closure ends (decided by the product
owner on 2026-10-25), instead of 24 hours after the slot.

    visible ─┬─ upcoming  (now < starts_at)       banner, still bookable before starts_at
             └─ active    (starts_at <= now < end) banner, nothing bookable inside it
    ended               (end <= now)              no banner; suspended bookings remain
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.errors import DomainError, ErrorCode

MAX_NOTICE_LENGTH = 1000
"""Longest notice text per locale; enough for a few sentences, short enough for a banner."""

NOTICE_LOCALES = ("nl", "en")
"""Locales a notice must be given in: Dutch is the default, English is required (CLAUDE.md)."""

RETENTION_AFTER_CLOSURE = timedelta(days=30)
"""How long a still suspended booking keeps its personal data after the closure ends."""

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_notice(raw: str, field_name: str) -> str:
    """Return the notice text trimmed, with Windows and old Mac line breaks made Unix ones.

    Raises:
        DomainError: ``invalid_configuration`` with ``field_name`` when the text is empty, too
            long or contains control characters other than line breaks and tabs.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or len(text) > MAX_NOTICE_LENGTH or _CONTROL_CHARS.search(text):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, field_name)
    return text


def notice_field(locale: str) -> str:
    """Return the form field of the notice in ``locale``, e.g. ``notice_nl``."""
    return f"notice_{locale}"


@dataclass(frozen=True)
class Closure:
    """An emergency closure of one group.

    Attributes:
        id: Database id; ``None`` before it is stored.
        group_id: The closed group.
        starts_at: First start time that is closed (UTC).
        ends_at: First start time that is open again (UTC); ``None`` until lifted.
        notices: The explanation for citizens per locale (at least ``NOTICE_LOCALES``). Plain
            text written by an admin; it never contains personal data.
        created_at: When an admin announced it.

    Raises:
        DomainError: ``invalid_configuration`` for a naive time, an end before the start or a
            missing or invalid notice.
    """

    id: int | None
    group_id: int
    starts_at: datetime
    ends_at: datetime | None
    notices: Mapping[str, str] = field(compare=False)
    created_at: datetime

    def __post_init__(self) -> None:
        """Validate the window and the notices."""
        if self.starts_at.tzinfo is None:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "starts_at")
        if self.ends_at is not None and (
            self.ends_at.tzinfo is None or self.ends_at < self.starts_at
        ):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "ends_at")
        for locale in NOTICE_LOCALES:
            clean_notice(self.notices.get(locale, ""), notice_field(locale))

    def covers(self, start: datetime) -> bool:
        """Return whether an appointment starting at ``start`` falls inside the closure."""
        return self.starts_at <= start and (self.ends_at is None or start < self.ends_at)

    def is_active(self, now: datetime) -> bool:
        """Return whether the group is closed at ``now``."""
        return self.covers(now)

    def is_visible(self, now: datetime) -> bool:
        """Return whether citizens see the notice at ``now`` (active or upcoming)."""
        return self.ends_at is None or now < self.ends_at

    def notice(self, locale: str) -> str:
        """Return the notice in ``locale``, falling back to Dutch."""
        return self.notices.get(locale) or self.notices[NOTICE_LOCALES[0]]

    @property
    def purge_at(self) -> datetime | None:
        """When the personal data of still suspended bookings goes; ``None`` while open-ended."""
        return self.ends_at + RETENTION_AFTER_CLOSURE if self.ends_at else None


def covering(closures: Iterable[Closure], start: datetime) -> Closure | None:
    """Return the closure that covers ``start``, if any."""
    return next((c for c in closures if c.covers(start)), None)
