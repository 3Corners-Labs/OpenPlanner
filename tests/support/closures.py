"""Shared builders for emergency closure tests (ADR 0023)."""

from dataclasses import replace

from app.domain.booking import Booking, BookingStatus, Contact
from app.domain.scheduling import PhoneMode
from tests.support.domain import NOW, SLOT_0900, SLOT_0930

NOTICES = {
    "nl": "Door een storing zijn wij gesloten.",
    "en": "We are closed because of an outage.",
}

_ADA = Contact.create(
    name="Ada Lovelace", email="ada@example.org", phone=None, phone_mode=PhoneMode.OPTIONAL
)


def confirmed_booking(**overrides: object) -> Booking:
    """Return a stored, confirmed booking of group 1 at Monday 09:00, with any field overridden."""
    booking = Booking(
        id=10,
        group_id=1,
        slot_id=5,
        start=SLOT_0900,
        end=SLOT_0930,
        status=BookingStatus.CONFIRMED,
        contact=_ADA,
        locale="nl",
        token_hash="h" * 64,
        created_at=NOW,
    )
    return replace(booking, **overrides)  # type: ignore[arg-type]


def suspended_booking(**overrides: object) -> Booking:
    """Return the booking of ``confirmed_booking`` suspended by closure 1."""
    return confirmed_booking(**overrides).suspend(1)
