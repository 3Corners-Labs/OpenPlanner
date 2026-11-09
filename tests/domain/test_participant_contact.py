"""The participant's contact details on the booking page: on request, audited (ADR 0044 §2)."""

from dataclasses import fields
from datetime import timedelta

import pytest

from app.domain.activity_alert import REVEALS_ALERT
from app.domain.admin import AdminService
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.authorization import Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.participant_contact import CONTACT_VIEWED, ParticipantContactService
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OTHER_TEAM
from tests.support.domain import book

ADA_VALUES = ("Ada", "Lovelace", "ada@example.org", "612345678")
"""Ada's name, address and the digits of her number: none of them may reach an audit event."""


def _service(
    uow: InMemoryUnitOfWork, clock: FixedClock, alert_per_hour: int = 20
) -> ParticipantContactService:
    admin = AdminService(uow, clock, alert_thresholds={REVEALS_ALERT: alert_per_hour})
    return ParticipantContactService(uow, clock, admin)


@pytest.fixture
def contacts(uow: InMemoryUnitOfWork, clock: FixedClock) -> ParticipantContactService:
    return _service(uow, clock)


async def _book(bookings: BookingService, slug: str = "demo") -> int:
    return (await book(bookings, slug=slug)).booking.id or 0


def _views(uow: InMemoryUnitOfWork) -> list[AuditEvent]:
    return [e for e in uow.state.audit if e.detail == CONTACT_VIEWED]


async def test_each_request_shows_the_contact_and_is_audited_without_the_data(
    contacts: ParticipantContactService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings)

    first = await contacts.reveal(GROUP_ADMIN, "demo", booking_id)
    again = await contacts.reveal(GROUP_ADMIN, "demo", booking_id)

    assert first is not None
    assert again == first
    assert (first.name, first.email, first.phone) == (
        "Ada Lovelace",
        "ada@example.org",
        "+31612345678",
    )
    views = _views(uow)
    assert [(e.actor, e.action, e.outcome, e.group, e.booking_id) for e in views] == [
        ("g-1", "bookings.view", Outcome.SUCCESS, "demo", booking_id)
    ] * 2
    written = [str(getattr(e, f.name)) for e in views for f in fields(e)]
    assert not [v for v in ADA_VALUES for text in written if v in text]


async def test_a_booking_of_another_group_is_not_found_through_this_group(
    contacts: ParticipantContactService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    other = await _book(bookings, "andere-team")

    with pytest.raises(DomainError) as caught:
        await contacts.reveal(GROUP_ADMIN, "demo", other)

    assert caught.value.code is ErrorCode.BOOKING_NOT_FOUND
    assert _views(uow) == []


async def test_an_unknown_booking_is_not_found(
    contacts: ParticipantContactService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as caught:
        await contacts.reveal(GROUP_ADMIN, "demo", 999)

    assert caught.value.code is ErrorCode.BOOKING_NOT_FOUND
    assert _views(uow) == []


@pytest.mark.parametrize("principal", [AUDITOR, OTHER_TEAM], ids=["auditor", "other team"])
async def test_without_bookings_view_on_the_group_nothing_is_shown_and_the_denial_audited(
    principal: Principal,
    contacts: ParticipantContactService,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
) -> None:
    booking_id = await _book(bookings)

    with pytest.raises(DomainError) as caught:
        await contacts.reveal(principal, "demo", booking_id)

    assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert _views(uow) == []
    assert [(e.action, e.outcome) for e in uow.state.audit] == [("bookings.view", Outcome.DENIED)]


async def test_after_the_purge_there_is_nothing_to_show_and_nothing_is_audited(
    contacts: ParticipantContactService,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    booking_id = await _book(bookings)
    async with uow.locked() as tx:
        await tx.purge_personal_data(booking_id, clock.now())

    assert await contacts.reveal(GROUP_ADMIN, "demo", booking_id) is None
    assert _views(uow) == []


# --- the hourly alert (contact_reveals_alert_per_hour) -------------------------------------


def _alerts(uow: InMemoryUnitOfWork) -> list[AuditEvent]:
    return [e for e in uow.state.audit if e.action == REVEALS_ALERT.action]


async def test_the_request_past_the_hourly_limit_raises_one_system_event_and_still_shows(
    uow: InMemoryUnitOfWork, clock: FixedClock, bookings: BookingService
) -> None:
    contacts = _service(uow, clock, alert_per_hour=2)
    booking_id = await _book(bookings)

    shown = [await contacts.reveal(GROUP_ADMIN, "demo", booking_id) for _ in range(4)]

    assert None not in shown
    (alert,) = _alerts(uow)
    assert (alert.actor, alert.source, alert.outcome, alert.reason) == (
        SYSTEM_ACTOR,
        Source.SYSTEM,
        Outcome.FAILURE,
        "too_many_contact_reveals",
    )
    assert (alert.organisation, alert.group, alert.booking_id) == ("demo", "demo", None)
    counted = {"bookings.view", REVEALS_ALERT.action}
    assert [e.detail for e in uow.state.audit if e.action in counted] == [
        CONTACT_VIEWED,
        CONTACT_VIEWED,
        CONTACT_VIEWED,
        "actor:g-1,reveals:3",
        CONTACT_VIEWED,
    ]


async def test_requests_older_than_an_hour_no_longer_count(
    uow: InMemoryUnitOfWork, clock: FixedClock, bookings: BookingService
) -> None:
    contacts = _service(uow, clock, alert_per_hour=1)
    booking_id = await _book(bookings)

    await contacts.reveal(GROUP_ADMIN, "demo", booking_id)
    clock.advance(timedelta(hours=1, seconds=1))
    await contacts.reveal(GROUP_ADMIN, "demo", booking_id)

    assert _alerts(uow) == []


async def test_only_shown_contact_details_count_not_a_refused_request(
    uow: InMemoryUnitOfWork, clock: FixedClock, bookings: BookingService
) -> None:
    contacts = _service(uow, clock, alert_per_hour=1)
    booking_id = await _book(bookings)
    other = await _book(bookings, "andere-team")

    with pytest.raises(DomainError):
        await contacts.reveal(GROUP_ADMIN, "andere-team", other)
    await contacts.reveal(GROUP_ADMIN, "demo", booking_id)

    assert [(e.action, e.outcome) for e in uow.state.audit if e.actor == "g-1"] == [
        ("bookings.view", Outcome.DENIED),
        ("bookings.view", Outcome.SUCCESS),
    ]
    assert _alerts(uow) == []


async def test_each_person_is_counted_on_their_own(
    uow: InMemoryUnitOfWork, clock: FixedClock, bookings: BookingService
) -> None:
    contacts = _service(uow, clock, alert_per_hour=1)
    booking_id = await _book(bookings)

    await contacts.reveal(GROUP_ADMIN, "demo", booking_id)
    await contacts.reveal(ORG_ADMIN, "demo", booking_id)
    assert _alerts(uow) == []

    await contacts.reveal(ORG_ADMIN, "demo", booking_id)
    assert [e.detail for e in _alerts(uow)] == ["actor:o-1,reveals:2"]
