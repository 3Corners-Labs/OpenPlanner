"""BookingService: the participant corrects their details (ADR 0056 point 10).

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; the group has places on Monday 09:00
Amsterdam. The participant may correct name, phone and answers until the change deadline.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import FieldDefinition, FieldType, FormDefinition, Purpose
from app.domain.scheduling import ScheduleRule
from app.domain.services import DETAILS_CORRECTED, BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import (
    ADA,
    NOW,
    SLOT_0900,
    TEST_TOKEN_KEY,
    book,
    book_pending,
    make_group,
    window,
)

CUSTOMER = FieldDefinition.create(
    key="customer_no",
    type=FieldType.SHORT_TEXT,
    labels={"nl": "Klantnummer", "en": "Customer number"},
    required=True,
)
ADA_WITH_ANSWER = replace(ADA, answers={"customer_no": "A-12"})


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.groups[1] = make_group(id=1, slug="balie")
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    state.forms[1] = FormDefinition(1, (CUSTOMER,))
    return InMemoryUnitOfWork(state)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def service(uow: InMemoryUnitOfWork, clock: FixedClock) -> BookingService:
    return BookingService(uow, clock, TEST_TOKEN_KEY, phone_region=lambda _org: "NL")


async def test_name_phone_and_answers_are_corrected_and_the_email_stays(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(service, contact=ADA_WITH_ANSWER)
    sent = replace(
        ADA,
        first_name="Augusta",
        email="other@example.org",
        phone="06 8765 4321",
        answers={"customer_no": "B-7"},
    )

    corrected = await service.correct_details(receipt.management_token, sent)

    contact = corrected.contact
    assert contact is not None
    assert contact.first_name == "Augusta"
    assert contact.email == "ada@example.org"
    assert contact.phone == "+31687654321"
    assert contact.answers.visible(Purpose.PLANNER_VIEW) == {"customer_no": "B-7"}
    assert uow.state.bookings[corrected.id or 0].contact == contact
    event = uow.state.audit[-1]
    assert event.action == DETAILS_CORRECTED
    assert event.detail == "fields:first_name+phone+answers"
    assert "Augusta" not in repr(event)


async def test_a_booking_awaiting_confirmation_can_be_corrected(service: BookingService) -> None:
    receipt = await book_pending(service, contact=ADA_WITH_ANSWER)

    corrected = await service.correct_details(
        receipt.management_token, replace(ADA_WITH_ANSWER, last_name="Byron")
    )

    assert corrected.contact is not None
    assert corrected.contact.last_name == "Byron"
    assert corrected.awaits_confirmation


async def test_nothing_changed_records_nothing(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(service, contact=ADA_WITH_ANSWER)
    events = len(uow.state.audit)

    await service.correct_details(receipt.management_token, ADA_WITH_ANSWER)

    assert len(uow.state.audit) == events


async def test_invalid_details_are_refused_and_nothing_changes(
    service: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(service, contact=ADA_WITH_ANSWER)
    booking_id = receipt.booking.id or 0
    before = uow.state.bookings[booking_id].contact

    with pytest.raises(DomainError) as caught:
        await service.correct_details(receipt.management_token, replace(ADA, first_name="Augusta"))

    assert caught.value.code is ErrorCode.INVALID_ANSWER
    assert uow.state.bookings[booking_id].contact == before


async def test_after_the_change_deadline_the_details_are_locked(
    service: BookingService, clock: FixedClock
) -> None:
    receipt = await book(service, contact=ADA_WITH_ANSWER)
    clock.advance(SLOT_0900 - timedelta(minutes=1) - NOW)

    with pytest.raises(DomainError) as caught:
        await service.correct_details(receipt.management_token, ADA_WITH_ANSWER)

    assert caught.value.code is ErrorCode.BOOKING_LOCKED
    assert not (await service.find(receipt.management_token)).correctable


async def test_a_cancelled_booking_cannot_be_corrected(service: BookingService) -> None:
    receipt = await book(service, contact=ADA_WITH_ANSWER)
    await service.cancel(receipt.management_token)

    with pytest.raises(DomainError) as caught:
        await service.correct_details(receipt.management_token, ADA_WITH_ANSWER)

    assert caught.value.code is ErrorCode.BOOKING_NOT_ACTIVE
    assert not (await service.find(receipt.management_token)).correctable


async def test_an_open_booking_is_correctable(service: BookingService) -> None:
    receipt = await book(service, contact=ADA_WITH_ANSWER)

    assert (await service.find(receipt.management_token)).correctable
