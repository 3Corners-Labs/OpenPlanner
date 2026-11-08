"""The participant's contact details on the booking page: on request, audited (ADR 0044 §2)."""

from dataclasses import fields

import pytest

from app.domain.admin import AdminService
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.participant_contact import CONTACT_VIEWED, ParticipantContactService
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, OTHER_TEAM
from tests.support.domain import book

ADA_VALUES = ("Ada", "Lovelace", "ada@example.org", "612345678")
"""Ada's name, address and the digits of her number: none of them may reach an audit event."""


@pytest.fixture
def contacts(uow: InMemoryUnitOfWork, clock: FixedClock) -> ParticipantContactService:
    return ParticipantContactService(uow, clock, AdminService(uow, clock))


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
