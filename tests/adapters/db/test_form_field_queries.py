"""SQL queries of a group's booking form and the stored answers (ADR 0055)."""

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import BookingRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.form_fields import (
    Classification,
    FieldDefinition,
    FieldType,
    FormDefinition,
    Purpose,
)
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import ADA, NOW, SLOT_0900, TEST_TOKEN_KEY

TOPIC = FieldDefinition.create(
    key="topic",
    type=FieldType.CHOICE,
    labels={"nl": "Onderwerp", "en": "Subject"},
    hints={"nl": "Waar gaat het over?"},
    options=("rent", "care"),
    option_labels={"rent": {"nl": "Huur", "en": "Rent"}, "care": {"nl": "Zorg", "en": "Care"}},
    classification=Classification.NOT_PII,
    required=True,
)
PEOPLE = FieldDefinition.create(
    key="people",
    type=FieldType.NUMBER,
    labels={"nl": "Aantal", "en": "Number"},
    minimum=1,
    maximum=4,
)


async def test_a_group_without_fields_has_an_empty_first_version(engine: AsyncEngine) -> None:
    group_id = await seed_group(engine)

    async with SqlUnitOfWork(engine).read() as tx:
        assert await tx.get_form(group_id) == FormDefinition(1)


async def test_the_form_is_stored_in_order_with_its_retired_fields(engine: AsyncEngine) -> None:
    group_id = await seed_group(engine)
    old = replace(PEOPLE, key="old_people", retired=True)
    form = FormDefinition(4, (PEOPLE, TOPIC), (old,))
    uow = SqlUnitOfWork(engine)

    async with uow.locked() as tx:
        await tx.save_form(group_id, form)
    async with uow.read() as tx:
        assert await tx.get_form(group_id) == form
    async with uow.locked() as tx:
        await tx.save_form(group_id, FormDefinition(5, (TOPIC,), (old,)))
    async with uow.read() as tx:
        stored = await tx.get_form(group_id)
    assert (stored.version, [f.key for f in stored.fields]) == (5, ["topic"])


async def test_answers_are_stored_raised_to_pii_and_purged(engine: AsyncEngine) -> None:
    group_id = await seed_group(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_form(group_id, FormDefinition(2, (TOPIC, PEOPLE)))
    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)
    receipt = await service.book(
        group_slug="aangifte",
        start=SLOT_0900,
        contact=replace(ADA, answers={"topic": "rent", "people": "2"}),
        locale="nl",
    )
    booking_id = receipt.booking.id or 0

    async with uow.read() as tx:
        stored = await tx.get_booking(booking_id)
    assert stored is not None
    assert stored.contact is not None
    assert stored.contact.answers is not None
    assert stored.contact.answers.visible(Purpose.PLANNER_VIEW) == {"topic": "rent", "people": 2}
    assert stored.contact.answers.visible(Purpose.LOG) == {"topic": "rent"}

    async with uow.locked() as tx:
        assert await tx.raise_answers_to_pii(group_id, "topic") == 1
        assert await tx.raise_answers_to_pii(group_id, "topic") == 0
    async with uow.read() as tx:
        raised = await tx.get_booking(booking_id)
    assert raised is not None
    assert raised.contact is not None
    assert raised.contact.answers is not None
    assert raised.contact.answers.visible(Purpose.LOG) == {}

    async with uow.locked() as tx:
        await tx.purge_personal_data(booking_id, NOW)
    async with engine.connect() as conn:
        left = (await conn.execute(select(BookingRow.form_answers))).scalar_one()
    assert left is None
