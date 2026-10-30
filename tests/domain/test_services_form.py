"""BookingService with custom form fields and the organisation phone region (ADR 0055)."""

from dataclasses import replace

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import (
    Classification,
    FieldDefinition,
    FieldType,
    FormDefinition,
    Purpose,
)
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import ADA, NOW, SLOT_0900, TEST_TOKEN_KEY, make_group, window

CUSTOMER = FieldDefinition.create(
    key="customer_no",
    type=FieldType.SHORT_TEXT,
    labels={"nl": "Klantnummer", "en": "Customer number"},
    required=True,
)
SINCE = FieldDefinition.create(
    key="moved_on",
    type=FieldType.DATE,
    labels={"nl": "Verhuisd op", "en": "Moved on"},
    minimum=-30,
    maximum=0,
    classification=Classification.NOT_PII,
)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.groups[1] = make_group(id=1, slug="aangifte")
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    state.forms[1] = FormDefinition(2, (CUSTOMER, SINCE))
    return InMemoryUnitOfWork(state)


def _service(uow: InMemoryUnitOfWork, region: str = "NL") -> BookingService:
    return BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, phone_region=lambda _org: region)


async def _book(service: BookingService, **answers: str) -> object:
    return await service.book(
        group_slug="aangifte",
        start=SLOT_0900,
        contact=replace(ADA, answers=answers),
        locale="nl",
    )


async def test_answers_are_validated_and_stored_with_the_booking(uow: InMemoryUnitOfWork) -> None:
    receipt = await _book(_service(uow), customer_no=" A-12 ", moved_on="2025-12-20")

    contact = receipt.booking.contact  # type: ignore[attr-defined]
    assert contact.answers.version == 2
    assert contact.answers.visible(Purpose.PLANNER_VIEW) == {
        "customer_no": "A-12",
        "moved_on": "2025-12-20",
    }
    assert contact.answers.visible(Purpose.LOG) == {"moved_on": "2025-12-20"}
    assert (await _service(uow).get_form(uow.state.groups[1])).version == 2


async def test_dates_are_bounded_by_the_day_in_the_group_time_zone(
    uow: InMemoryUnitOfWork,
) -> None:
    with pytest.raises(DomainError) as caught:
        await _book(_service(uow), customer_no="A-12", moved_on="2025-11-30")

    assert str(caught.value) == "invalid_answer: moved_on"
    assert uow.state.bookings == {}


async def test_a_missing_required_answer_stores_nothing(uow: InMemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as caught:
        await _book(_service(uow))

    assert caught.value.code is ErrorCode.INVALID_ANSWER
    assert caught.value.field == "customer_no"
    assert uow.state.bookings == {} and uow.state.slots == {}  # noqa: PT018


async def test_an_answer_to_a_field_the_form_does_not_have_is_refused(
    uow: InMemoryUnitOfWork,
) -> None:
    with pytest.raises(DomainError) as caught:
        await _book(_service(uow), customer_no="A-12", bsn="123456782")

    assert str(caught.value) == "invalid_answer: unknown"


async def test_without_custom_fields_there_are_no_answers(uow: InMemoryUnitOfWork) -> None:
    del uow.state.forms[1]

    receipt = await _book(_service(uow))

    assert receipt.booking.contact.answers is None  # type: ignore[attr-defined]


async def test_the_organisation_region_reads_the_phone_number(uow: InMemoryUnitOfWork) -> None:
    del uow.state.forms[1]
    service = _service(uow, region="BE")

    receipt = await service.book(
        group_slug="aangifte",
        start=SLOT_0900,
        contact=replace(ADA, phone="0470 12 34 56"),
        locale="nl",
    )

    assert receipt.booking.contact.phone == "+32470123456"  # type: ignore[union-attr]
