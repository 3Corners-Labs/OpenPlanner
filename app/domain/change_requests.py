"""Change requests through the machine API and the appointment code (ADR 0023 step 2).

A contact-center bot asks to cancel or move a booking for a caller. The caller names the
booking by its phone number and its appointment code together (product owner, 2026-11-09);
the change only happens when the participant confirms it through their own message. This
module holds the rules: the code, the request and its status. It knows no framework and no
storage; ``ChangeRequestService`` applies them.

The code is derived like the management token (ADR 0011): HMAC-SHA256 under the server key of
the booking's salt, with its own purpose, reduced to ``CODE_DIGITS`` digits. Nothing is stored
for it and a new booking (also a reschedule) gets a new code. After the purge the booking has
no phone number and is no longer confirmed, so its code matches nothing any more.
"""

import hashlib
import hmac
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.booking import Booking
from app.domain.tokens import MIN_KEY_BYTES, key_for_booking

CODE_DIGITS = 8
"""Digits of an appointment code: a crypto parameter, fixed (ADR 0023 step 2, *Settings*)."""

_CODE_PURPOSE = b"code:"
_ID_PREFIX = "chr_"
_ID_BYTES = 18


class ChangeAction(StrEnum):
    """What the caller asks for; a reschedule has a new start, a cancellation none."""

    CANCEL = "cancel"
    RESCHEDULE = "reschedule"


class ChangeStatus(StrEnum):
    """What ``GET /change-requests/{id}`` answers; the same for a match and a non-match."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


def derive_code(key: bytes, salt: str) -> str:
    """Return the appointment code of a booking with ``salt`` under ``key``, as digits.

    Raises:
        ValueError: If ``key`` is shorter than ``MIN_KEY_BYTES``.
    """
    if len(key) < MIN_KEY_BYTES:
        raise ValueError(f"the token key must be at least {MIN_KEY_BYTES} bytes")
    digest = hmac.new(key, _CODE_PURPOSE + salt.encode(), hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 10**CODE_DIGITS:0{CODE_DIGITS}d}"


def appointment_code(keys: Sequence[bytes], booking: Booking) -> str:
    """Return the code a booking shows, as the participant reads it: ``1234 5678``.

    Under the key its management token was made with (``key_for_booking``), so during a key
    rotation (ADR 0094) a booking keeps the code it was given.
    """
    key = key_for_booking(keys, booking.token_salt, booking.token_hash)
    return format_code(derive_code(key, booking.token_salt))


def code_matches(keys: Sequence[bytes], salt: str, code: str) -> bool:
    """Return whether ``code`` is the booking's code under any of ``keys``, in constant time."""
    found = False
    for key in keys:
        found |= hmac.compare_digest(derive_code(key, salt), code)
    return found


def normalise_code(raw: str) -> str | None:
    """Return the code without spaces, dashes or dots, or ``None`` when it is no code."""
    digits = "".join(c for c in raw if c not in " -.")
    if len(digits) != CODE_DIGITS or not digits.isascii() or not digits.isdigit():
        return None
    return digits


def format_code(code: str) -> str:
    """Return the code as the participant reads it: ``1234 5678``."""
    half = CODE_DIGITS // 2
    return f"{code[:half]} {code[half:]}"


def new_request_id() -> str:
    """Return a fresh public id of a change request; names it, authorises nothing alone."""
    return _ID_PREFIX + secrets.token_urlsafe(_ID_BYTES)


@dataclass(frozen=True)
class ChangeRequest:
    """One request of a bot to change a booking; no personal data.

    Attributes:
        public_id: What the API returns and the client asks the status with.
        client_id: The API client that made it; only it may read the status.
        group_id: The group of the URL.
        target_start: For a reschedule, the requested new start (UTC); ``None`` cancels.
        booking_id: The booking the phone number and code matched, or ``None``.
        pinned_start: That booking's start when asked; a request is void once it moved.
        created_at: When it was made.
        expires_at: Until when the participant can confirm it; the same rule for a match and
            a non-match, so it reveals nothing.
        confirmed_at: When the participant confirmed it.
        superseded_at: When a newer request for the same booking replaced it. Internal: the
            API's status ignores it, or it would tell which requests matched a booking.
        id: Database id.
    """

    public_id: str
    client_id: str
    group_id: int
    target_start: datetime | None
    booking_id: int | None
    pinned_start: datetime | None
    created_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None = None
    superseded_at: datetime | None = None
    id: int | None = None

    @property
    def action(self) -> ChangeAction:
        """Cancel, or reschedule to ``target_start``."""
        return ChangeAction.CANCEL if self.target_start is None else ChangeAction.RESCHEDULE

    def status(self, now: datetime) -> ChangeStatus:
        """Return the status the API shows at ``now``; a replaced request looks unchanged."""
        if self.confirmed_at is not None:
            return ChangeStatus.CONFIRMED
        if now >= self.expires_at:
            return ChangeStatus.EXPIRED
        return ChangeStatus.PENDING

    def is_current(self, booking_start: datetime, now: datetime) -> bool:
        """Return whether the participant can still confirm it for a booking at that start.

        It matched a booking, is neither confirmed, replaced nor expired, and the booking has
        not moved since.
        """
        return (
            self.booking_id is not None
            and self.superseded_at is None
            and self.status(now) is ChangeStatus.PENDING
            and self.pinned_start == booking_start
        )

    def confirm(self, now: datetime) -> "ChangeRequest":
        """Return the request as confirmed at ``now``."""
        return replace(self, confirmed_at=now)

    def supersede(self, now: datetime) -> "ChangeRequest":
        """Return the request replaced at ``now`` by a newer one for the same booking."""
        return replace(self, superseded_at=now)


def request_expiry(now: datetime, minutes: int) -> datetime:
    """Return when a request made at ``now`` expires (``agent_change_request_minutes``)."""
    return now + timedelta(minutes=minutes)
