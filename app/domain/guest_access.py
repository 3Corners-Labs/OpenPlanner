"""The participant's guest identity at the video provider (ADR 0123 points 6, 7 and 17).

A participant who joins in the call page of our own gets a guest token from the provider, minted
by our server for one booking. Three rules live here, free of any adapter:

    subject   ``HMAC-SHA256(key, booking id)``, hex, under a fixed prefix: stable for one booking
              (a reload reuses the guest), different for every booking, and without the key it
              says nothing, so it links no two bookings and no person
    name      what the staff member sees: the participant's first name, or the organisation's
              word for a participant; a purged booking has no first name and falls back to
              the word
    check     one fixed subject for the service-app and group checks, so the checks create
              one guest in all and no more

The token itself is a bearer credential. It is never stored or logged, and ``GuestAccess`` keeps
it out of its ``repr``.
"""

import hashlib
import hmac
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

SUBJECT_PREFIX = "op1-"
"""Marks a subject as ours when it is read at the provider; the digit versions the derivation."""

SUBJECT_HEX = 32
"""Length of the digest in a subject, in hex characters (128 bits): a crypto parameter."""

MAX_DISPLAY_NAME = 40
"""Longest guest name sent to the provider; the length of one term word (ADR 0077)."""

CHECK_SUBJECT = "openplanner-check"
"""The one subject of the checks (ADR 0123 point 17). It cannot equal a booking's subject, which
is ``SUBJECT_PREFIX`` and a digest, and it names no one."""

NEUTRAL_TERM = "Participant"
"""The word the checks send as the guest's name: no participant, so no personal data."""


class DisplayNameMode(StrEnum):
    """What the guest is called (``embedded_call_display_name``)."""

    FIRST_NAME = "first_name"
    TERM = "term"


class StaffNameMode(StrEnum):
    """How the staff member is named to the participant (``embedded_call_staff_name``)."""

    NAME = "name"
    TERM = "term"


@dataclass(frozen=True)
class GuestAccess:
    """A guest token for one call.

    Attributes:
        token: The bearer token. Excluded from ``repr`` so it cannot be printed by accident.
        expires_at: When the provider stops accepting it (UTC, timezone-aware).
    """

    token: str = field(repr=False)
    expires_at: datetime


def guest_subject(booking_id: int, key: bytes) -> str:
    """Return the guest's subject for a booking: a keyed digest, not the booking id.

    Args:
        booking_id: The booking the guest belongs to.
        key: The server key (``app.keys.guest_pseudonym_key``).
    """
    digest = hmac.new(key, f"guest:{booking_id}".encode(), hashlib.sha256).hexdigest()
    return SUBJECT_PREFIX + digest[:SUBJECT_HEX]


def guest_display_name(first_name: str | None, mode: DisplayNameMode, term: str) -> str:
    """Return the name the guest joins under, cleaned and at most ``MAX_DISPLAY_NAME`` long.

    Args:
        first_name: The booking's first name; ``None`` or empty after a purge.
        mode: ``FIRST_NAME`` uses the first name when there is one, ``TERM`` never does.
        term: The organisation's word for a participant, for ``TERM`` and as the fallback; when
            it is empty too, ``NEUTRAL_TERM``, so the name is never empty.
    """
    wanted = first_name if mode is DisplayNameMode.FIRST_NAME else None
    return _clean(wanted) or _clean(term) or NEUTRAL_TERM


def _clean(text: str | None) -> str:
    """Return ``text`` without control characters, trimmed and cut at ``MAX_DISPLAY_NAME``."""
    kept = "".join(c for c in (text or "") if unicodedata.category(c)[0] != "C")
    return kept.strip()[:MAX_DISPLAY_NAME].strip()
