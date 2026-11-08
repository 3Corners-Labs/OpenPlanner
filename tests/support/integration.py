"""The call integration's test data (ADR 0018, 0021): a wired group and a booking in it."""

from dataclasses import replace
from datetime import timedelta

from app.domain.booking import Booking, BookingStatus
from tests.support.domain import ADA_CONTACT, NOW, SLOT_0900, make_group, webex_routing

GROUP = make_group(
    id=1,
    organisation_id=1,
    slug="demo",
    name="Demo: videogesprek",
    video_host_email="host-demo@example.nl",
    contact_center=webex_routing("openplanner@example.biz", "openplanner-video"),
)
"""Group ``demo`` with a video host and contact-centre routing: everything a call needs."""


def make_booking(**changes: object) -> Booking:
    """Return confirmed booking 123 of group 1 at Monday 09:00 for Ada, with any field changed."""
    booking = Booking(
        id=123,
        group_id=1,
        slot_id=7,
        start=SLOT_0900,
        end=SLOT_0900 + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
        contact=ADA_CONTACT,
        locale="nl",
        token_hash="h",
        created_at=NOW,
    )
    return replace(booking, **changes)  # type: ignore[arg-type]
