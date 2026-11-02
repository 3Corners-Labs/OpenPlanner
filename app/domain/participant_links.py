"""The participant link session: the rules (ADR 0093).

The participant opens the link from the email once, `GET /b/{token}`. The server checks the
token (ADR 0011), remembers on its side that this browser holds the booking, and sends the
browser on to a page whose address names the booking by its public reference and holds no
credential. The browser keeps only a random value in a cookie; the server keeps its SHA-256.
The session proves possession of the link, not an identity: participants never log in.

A session holds at most ``MAX_BOOKINGS`` bookings (the oldest drops out) and lives
``lifetime`` after its last use. The rules only; ``app.domain.participant_link_service``
applies them to the database.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

VALUE_BYTES = 32
"""Size of the random cookie value (a fixed crypto parameter, ADR 0093 point 2)."""

REF_BYTES = 16
"""Size of a booking's public reference (a fixed crypto parameter, ADR 0093 point 1)."""

MAX_BOOKINGS = 20
"""Most bookings one session holds; an abuse bound, not a tuning value (ADR 0093 point 5)."""

TOUCH_EVERY = timedelta(minutes=1)
"""How often a busy session writes its extension; a request inside it does not write."""


def new_value() -> str:
    """Return a fresh random cookie value."""
    return secrets.token_urlsafe(VALUE_BYTES)


def new_public_ref() -> str:
    """Return a fresh public reference: names a booking in a URL, authorises nothing."""
    return secrets.token_urlsafe(REF_BYTES)


def hash_value(value: str) -> str:
    """Return the hex SHA-256 of a cookie value, the only form that is stored."""
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class LinkSession:
    """One participant link session as the database holds it.

    Attributes:
        id: Row id.
        value_hash: ``sha256`` of the cookie value, hex.
        booking_ids: The bookings the browser holds, oldest first.
        last_used_at: When the session last extended itself.
        expires_at: When it lapses unless used again.
    """

    id: int
    value_hash: str
    booking_ids: tuple[int, ...]
    last_used_at: datetime
    expires_at: datetime

    def alive(self, now: datetime) -> bool:
        """Return whether the session has not lapsed."""
        return now < self.expires_at

    def due_for_touch(self, now: datetime) -> bool:
        """Return whether a request now should write the extension."""
        return now - self.last_used_at >= TOUCH_EVERY

    def holds(self, booking_id: int) -> bool:
        """Return whether the browser may use the booking."""
        return booking_id in self.booking_ids


def overflow(booking_ids: tuple[int, ...]) -> tuple[int, ...]:
    """Return the oldest ids that no longer fit under ``MAX_BOOKINGS``."""
    return booking_ids[: max(0, len(booking_ids) - MAX_BOOKINGS)]


@dataclass(frozen=True)
class PublicRef:
    """A booking named by its public reference, after the caller checked the link session.

    A service takes this where it takes a management token: it says "this browser may act on
    that booking". Never build one from a request without ``ParticipantLinkService.allows``.
    """

    value: str
