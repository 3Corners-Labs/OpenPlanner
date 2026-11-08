"""The call integration's test data (ADR 0018, 0021): a wired group and a booking in it."""

from dataclasses import replace
from datetime import timedelta

from app.domain.booking import Booking, BookingStatus, Contact
from app.domain.scheduling import PhoneMode
from tests.support.domain import NOW, SLOT_0900, make_group

GROUP = make_group(
    id=1,
    organisation_id=1,
    slug="demo",
    name="Demo: videogesprek",
    video_host_email="host-demo@example.nl",
    wxcc_business_address="openplanner@example.biz",
    wxcc_channel="openplanner-video",
)
"""Group ``demo`` with a video host and contact-centre routing: everything a call needs."""
ADA = Contact.create(
    first_name="Ada",
    last_name="Lovelace",
    email="ada@example.org",
    phone=None,
    phone_mode=PhoneMode.OPTIONAL,
)
"""Ada as a booking stores her, without a phone number."""


def make_booking(**changes: object) -> Booking:
    """Return confirmed booking 123 of group 1 at Monday 09:00 for Ada, with any field changed."""
    booking = Booking(
        id=123,
        group_id=1,
        slot_id=7,
        start=SLOT_0900,
        end=SLOT_0900 + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
        contact=ADA,
        locale="nl",
        token_hash="h",
        created_at=NOW,
    )
    return replace(booking, **changes)  # type: ignore[arg-type]
