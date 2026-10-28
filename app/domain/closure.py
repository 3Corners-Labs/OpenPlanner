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

Besides the notice a closure may carry a heading per locale and its own copy of a contact block
(ADR 0037 §2): the block that applied when it started, so what citizens were mailed stays what
the closure shows when an organisation or group later edits its block.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.contacts import ContactBlock
from app.domain.errors import DomainError, ErrorCode

MAX_NOTICE_LENGTH = 1000
"""Longest notice text per locale; enough for a few sentences, short enough for a banner."""

MAX_HEADING_LENGTH = 120
"""Longest heading per locale (ADR 0037 §6)."""

NOTICE_LOCALES = ("nl", "en")
"""Locales a notice must be given in: Dutch is the default, English is required (CLAUDE.md)."""

RETENTION_AFTER_CLOSURE = timedelta(days=30)
"""How long a still suspended booking keeps its personal data after the closure ends."""

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_BREAKS_OR_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


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


def clean_headings(raw: Mapping[str, str]) -> dict[str, str]:
    """Return the non-empty headings trimmed; an empty heading means "use the default".

    Raises:
        DomainError: ``invalid_configuration`` with ``heading_<locale>`` for a heading that is
            too long, has a line break or control character, or is in an unknown locale.
    """
    kept: dict[str, str] = {}
    for locale, text in raw.items():
        heading = text.strip()
        if not heading:
            continue
        if (
            locale not in NOTICE_LOCALES
            or len(heading) > MAX_HEADING_LENGTH
            or _LINE_BREAKS_OR_CONTROL.search(heading)
        ):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, heading_field(locale))
        kept[locale] = heading
    return kept


def heading_field(locale: str) -> str:
    """Return the form field of the heading in ``locale``, e.g. ``heading_nl``."""
    return f"heading_{locale}"


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
        headings: Optional heading per locale; empty means the page's default heading.
        contacts: The copy of the contact block taken when the closure started (or refreshed
            by an update); ``None`` when no block applied.

    Raises:
        DomainError: ``invalid_configuration`` for a naive time, an end before the start, a
            missing or invalid notice or an invalid heading.
    """

    id: int | None
    group_id: int
    starts_at: datetime
    ends_at: datetime | None
    notices: Mapping[str, str] = field(compare=False)
    created_at: datetime
    headings: Mapping[str, str] = field(default_factory=dict, compare=False)
    contacts: ContactBlock | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        """Validate the window, the notices and the headings; keep only non-empty headings."""
        if self.starts_at.tzinfo is None:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "starts_at")
        if self.ends_at is not None and (
            self.ends_at.tzinfo is None or self.ends_at < self.starts_at
        ):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "ends_at")
        for locale in NOTICE_LOCALES:
            clean_notice(self.notices.get(locale, ""), notice_field(locale))
        object.__setattr__(self, "headings", clean_headings(self.headings))

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

    def heading(self, locale: str) -> str | None:
        """Return the heading in ``locale``, falling back to Dutch, or ``None``."""
        return self.headings.get(locale) or self.headings.get(NOTICE_LOCALES[0])

    @property
    def purge_at(self) -> datetime | None:
        """When the personal data of still suspended bookings goes; ``None`` while open-ended."""
        return self.ends_at + RETENTION_AFTER_CLOSURE if self.ends_at else None


def covering(closures: Iterable[Closure], start: datetime) -> Closure | None:
    """Return the closure that covers ``start``, if any."""
    return next((c for c in closures if c.covers(start)), None)
