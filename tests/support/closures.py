"""Shared builders for emergency closure tests (ADR 0024)."""

from dataclasses import replace

from app.domain.booking import Booking, BookingStatus, Contact
from app.domain.scheduling import Organisation, PhoneMode, ScheduleRule
from tests.fakes.persistence import State
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, make_group, window

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


def three_groups() -> State:
    """Return a state with groups ``demo`` and ``demo-twee`` (organisation ``demo``) and
    ``andere-team`` (organisation ``andere``), each staffed Monday 09:00-10:00 by 2 agents."""
    state = State()
    state.organisation_slugs = {1: "demo", 2: "andere"}
    state.organisations = {
        1: Organisation(1, "Demo", "demo"),
        2: Organisation(2, "Andere", "andere"),
    }
    state.groups[1] = make_group(id=1, organisation_id=1, slug="demo", horizon_days=30)
    state.groups[2] = make_group(id=2, organisation_id=2, slug="andere-team")
    state.groups[3] = make_group(id=3, organisation_id=1, slug="demo-twee")
    for group_id in state.groups:
        state.rules[group_id] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    state.next_id = 100
    return state
