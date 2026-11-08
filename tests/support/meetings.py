"""The meeting request of the video adapter tests: the demo slot, hosted by ``host@example.nl``."""

from datetime import timedelta

from app.domain.calls import MeetingRequest
from tests.support.domain import SLOT_0900

REQUEST = MeetingRequest(
    "Videogesprek Demo",
    SLOT_0900,
    SLOT_0900 + timedelta(minutes=30),
    "Europe/Amsterdam",
    "host@example.nl",
)
"""A 30-minute meeting from Monday 09:00 Amsterdam (``SLOT_0900``)."""
