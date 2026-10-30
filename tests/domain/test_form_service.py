"""FormService (ADR 0055): who changes a group's form, what is audited, and the PII tag."""

from dataclasses import replace

import pytest

from app.domain import audit
from app.domain.admin import AdminService
from app.domain.booking import Contact
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import (
    Answers,
    Classification,
    FieldDefinition,
    FieldType,
    FormDefinition,
    Purpose,
    validate_answers,
)
from app.domain.form_service import FormService
from tests.fakes import clock as fake_clock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.closures import confirmed_booking as make_booking
from tests.support.domain import NOW

LABELS = {"nl": "Vraag", "en": "Question"}


def field(key: str, **overrides: object) -> FieldDefinition:
    values: dict[str, object] = {"key": key, "type": FieldType.SHORT_TEXT, "labels": LABELS}
    values.update(overrides)
    return FieldDefinition.create(**values)  # type: ignore[arg-type]


def topic(classification: Classification = Classification.NOT_PII) -> FieldDefinition:
    return field(
        "topic",
        type=FieldType.CHOICE,
        options=("rent",),
        option_labels={"rent": {"nl": "Huur", "en": "Rent"}},
        classification=classification,
    )


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def _service(uow: InMemoryUnitOfWork, limit: int = 3) -> FormService:
    clock = fake_clock.FixedClock(NOW)
    return FormService(uow, clock, AdminService(uow, clock), lambda: limit)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> FormService:
    return _service(uow)


def details(uow: InMemoryUnitOfWork) -> list[str]:
    return [e.detail for e in uow.state.audit if e.outcome is audit.Outcome.SUCCESS]


async def test_a_group_starts_without_custom_fields(service: FormService) -> None:
    view = await service.view(ORG_ADMIN, "demo")

    assert (view.form.version, view.form.fields, view.can_add) == (1, (), True)
    assert view.max_custom_fields == 3


async def test_fields_are_added_in_order_and_audited_by_key(
    service: FormService, uow: InMemoryUnitOfWork
) -> None:
    await service.add(ORG_ADMIN, "demo", field("customer_no"), confirmed=False)
    await service.add(SUPER, "demo", field("reference"), confirmed=False)

    form = uow.state.forms[1]
    assert [f.key for f in form.fields] == ["customer_no", "reference"]
    assert form.version == 3
    assert details(uow) == [
        "field.add,key:customer_no,type:short_text",
        "field.add,key:reference,type:short_text",
    ]


@pytest.mark.parametrize("principal", [GROUP_ADMIN, OUTSIDER])
async def test_only_who_edits_the_group_may_change_its_form(
    service: FormService, uow: InMemoryUnitOfWork, principal: object
) -> None:
    with pytest.raises(DomainError) as caught:
        await service.add(principal, "demo", field("customer_no"), confirmed=False)  # type: ignore[arg-type]

    assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert 1 not in uow.state.forms
    assert uow.state.audit[-1].outcome is audit.Outcome.DENIED


async def test_an_unknown_group_is_refused(service: FormService) -> None:
    with pytest.raises(DomainError) as caught:
        await service.view(SUPER, "nope")
    assert caught.value.code is ErrorCode.GROUP_NOT_FOUND


async def test_not_pii_needs_a_confirmation(service: FormService, uow: InMemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as caught:
        await service.add(ORG_ADMIN, "demo", topic(), confirmed=False)
    assert str(caught.value) == "invalid_form_field: confirm_not_pii"

    await service.add(ORG_ADMIN, "demo", topic(), confirmed=True)
    assert uow.state.forms[1].fields[0].classification is Classification.NOT_PII


async def test_the_limit_holds_for_adding_but_not_for_editing(uow: InMemoryUnitOfWork) -> None:
    wide = _service(uow, limit=2)
    await wide.add(ORG_ADMIN, "demo", field("a1"), confirmed=False)
    await wide.add(ORG_ADMIN, "demo", field("a2"), confirmed=False)
    narrow = _service(uow, limit=1)

    with pytest.raises(DomainError) as caught:
        await narrow.add(ORG_ADMIN, "demo", field("a3"), confirmed=False)
    assert str(caught.value) == "invalid_form_field: fields"
    assert not (await narrow.view(ORG_ADMIN, "demo")).can_add
    assert await narrow.update(ORG_ADMIN, "demo", field("a1", required=True), confirmed=False)
    await narrow.move(ORG_ADMIN, "demo", "a2", -1)
    assert [f.key for f in uow.state.forms[1].fields] == ["a2", "a1"]


async def test_update_keeps_key_and_type_and_reports_a_change(
    service: FormService, uow: InMemoryUnitOfWork
) -> None:
    await service.add(ORG_ADMIN, "demo", field("customer_no"), confirmed=False)

    assert not await service.update(ORG_ADMIN, "demo", field("customer_no"), confirmed=False)
    assert await service.update(
        ORG_ADMIN, "demo", field("customer_no", required=True), confirmed=False
    )
    assert details(uow)[-1] == "field.update,key:customer_no,changed:required"
    with pytest.raises(DomainError) as caught:
        await service.update(
            ORG_ADMIN, "demo", field("customer_no", type=FieldType.NUMBER), confirmed=False
        )
    assert str(caught.value) == "invalid_form_field: type"
    with pytest.raises(DomainError) as unknown:
        await service.update(ORG_ADMIN, "demo", field("nope"), confirmed=False)
    assert str(unknown.value) == "invalid_form_field: key"


async def _booking_with_topic(uow: InMemoryUnitOfWork) -> int:
    form = FormDefinition(2, (topic(),))
    answers = validate_answers(form, {"topic": "rent"}, today=NOW.date(), region="NL")
    contact = Contact("Ada", "Lovelace", "ada@example.org", None, answers)
    booking = make_booking(id=50, group_id=1, contact=contact)
    uow.state.bookings[50] = booking
    return 50


def _logged(uow: InMemoryUnitOfWork, booking_id: int) -> dict[str, object]:
    contact = uow.state.bookings[booking_id].contact
    assert contact is not None
    assert contact.answers is not None
    return contact.answers.visible(Purpose.LOG)


async def test_raising_a_tag_makes_earlier_answers_pii_at_once(
    service: FormService, uow: InMemoryUnitOfWork
) -> None:
    await service.add(ORG_ADMIN, "demo", topic(), confirmed=True)
    booking_id = await _booking_with_topic(uow)
    assert _logged(uow, booking_id) == {"topic": "rent"}

    await service.update(ORG_ADMIN, "demo", topic(Classification.PII), confirmed=False)

    assert _logged(uow, booking_id) == {}


async def test_retiring_keeps_the_label_blocks_the_key_and_makes_answers_pii(
    service: FormService, uow: InMemoryUnitOfWork
) -> None:
    await service.add(ORG_ADMIN, "demo", topic(), confirmed=True)
    booking_id = await _booking_with_topic(uow)

    await service.retire(ORG_ADMIN, "demo", "topic")

    form = uow.state.forms[1]
    assert (form.fields, [f.key for f in form.retired]) == ((), ["topic"])
    assert _logged(uow, booking_id) == {}
    assert details(uow)[-1] == "field.retire,key:topic"
    with pytest.raises(DomainError) as caught:
        await service.add(ORG_ADMIN, "demo", topic(), confirmed=True)
    assert str(caught.value) == "invalid_form_field: key"
    with pytest.raises(DomainError):
        await service.retire(ORG_ADMIN, "demo", "topic")


async def test_moving_past_either_end_changes_nothing(
    service: FormService, uow: InMemoryUnitOfWork
) -> None:
    await service.add(ORG_ADMIN, "demo", field("a1"), confirmed=False)
    await service.add(ORG_ADMIN, "demo", field("a2"), confirmed=False)
    version = uow.state.forms[1].version

    await service.move(ORG_ADMIN, "demo", "a1", -1)
    await service.move(ORG_ADMIN, "demo", "a2", 1)

    assert uow.state.forms[1].version == version
    await service.move(ORG_ADMIN, "demo", "a1", 1)
    assert [f.key for f in uow.state.forms[1].fields] == ["a2", "a1"]
    assert details(uow)[-1] == "field.move,key:a1"


async def test_stored_answers_of_another_group_are_left_alone(uow: InMemoryUnitOfWork) -> None:
    answers = Answers.from_storage({"version": 1, "answers": {"x": "y"}, "not_pii": ["x"]})
    contact = Contact("Bo", "Berg", "bo@example.org", None, answers)
    uow.state.bookings[7] = replace(make_booking(id=7, group_id=2), contact=contact)

    async with uow.locked() as tx:
        assert await tx.raise_answers_to_pii(1, "x") == 0
