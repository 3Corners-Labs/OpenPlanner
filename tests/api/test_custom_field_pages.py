"""Custom form fields on the citizen pages and in the widget API (ADR 0055).

The demo group gets three fields: a required short text (PII), a choice that is not PII and
an optional checkbox. Calendar as in ``test_booking_pages``.
"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import BookingRow, GroupRow
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.form_fields import Classification, FieldDefinition, FieldType, FormDefinition
from tests.api.test_widget_api import _embed
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW

FRIDAY_0900 = "2026-01-02T08:00:00Z"
SAME_ORIGIN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
ADA = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.org", "phone": ""}
SITE = "https://www.gemeente.example"
WIDGET = "/api/v1/widget/demo/demo"

FIELDS = (
    FieldDefinition.create(
        key="customer_no",
        type=FieldType.SHORT_TEXT,
        labels={"nl": "Klantnummer", "en": "Customer number"},
        hints={"nl": "Staat op uw brief."},
        required=True,
    ),
    FieldDefinition.create(
        key="topic",
        type=FieldType.CHOICE,
        labels={"nl": "Onderwerp", "en": "Subject"},
        options=("rent", "care"),
        option_labels={"rent": {"nl": "Huur", "en": "Rent"}, "care": {"nl": "Zorg", "en": "Care"}},
        classification=Classification.NOT_PII,
    ),
    FieldDefinition.create(
        key="newsletter",
        type=FieldType.CHECKBOX,
        labels={"nl": "Nieuwsbrief", "en": "Newsletter"},
        classification=Classification.NOT_PII,
    ),
)


async def _with_form(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    async with AsyncSession(engine) as session:
        group_id = await session.scalar(select(GroupRow.id).where(GroupRow.slug == "demo"))
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.save_form(group_id or 0, FormDefinition(2, FIELDS))
    await engine.dispose()


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    await _with_form(demo_settings)
    await _embed(demo_settings)
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


async def stored_answers(settings: Settings) -> list[str | None]:
    engine = create_engine(settings.database_url)
    async with engine.connect() as conn:
        rows = (await conn.execute(select(BookingRow.form_answers))).scalars().all()
    await engine.dispose()
    return list(rows)


async def test_the_form_shows_every_custom_field_after_the_contact_fields(
    site: httpx.AsyncClient,
) -> None:
    text = (await site.get(f"/g/demo/book?start={FRIDAY_0900}")).text

    names = [
        n
        for n in ("first_name", "last_name", "email", "q_customer_no", "q_topic")
        if f'name="{n}"' in text
    ]
    assert names == ["first_name", "last_name", "email", "q_customer_no", "q_topic"]
    assert (
        text.index('name="email"')
        < text.index('name="q_customer_no"')
        < text.index('name="q_topic"')
    )
    assert 'autocomplete="given-name"' in text
    assert 'autocomplete="family-name"' in text
    assert '<p class="hint" id="q_customer_no-hint">Staat op uw brief.</p>' in text
    assert '<legend>Onderwerp <span class="optional">' in text
    assert '<input id="q_topic" name="q_topic" type="radio" value="rent">' in text
    assert '<label for="q_topic-2">Zorg</label>' in text
    assert 'type="checkbox" value="on"' in text
    assert "Nieuwsbrief" in text


async def test_answers_are_stored_with_the_booking(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    response = await site.post(
        "/g/demo/book",
        data={"start": FRIDAY_0900, **ADA, "q_customer_no": "A-1", "q_topic": "rent"},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 303
    (stored,) = await stored_answers(demo_settings)
    assert stored is not None
    assert json.loads(stored) == {
        "version": 2,
        "answers": {"customer_no": "A-1", "topic": "rent", "newsletter": False},
        "not_pii": ["newsletter", "topic"],
    }


async def test_a_refused_answer_names_the_question_and_keeps_the_input(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    response = await site.post(
        "/g/demo/book",
        data={"start": FRIDAY_0900, **ADA, "q_customer_no": "", "q_topic": "care"},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    text = response.text
    assert '<a href="#q_customer_no">Controleer uw antwoord bij ‘Klantnummer’.' in text
    assert 'aria-describedby="q_customer_no-hint q_customer_no-error"' in text
    assert 'id="q_topic-2" name="q_topic" type="radio" value="care" checked' in text
    assert await stored_answers(demo_settings) == []


async def test_an_unknown_answer_is_refused_without_naming_it(site: httpx.AsyncClient) -> None:
    response = await site.post(
        "/g/demo/book",
        data={"start": FRIDAY_0900, **ADA, "q_customer_no": "A-1", "q_bsn": "123"},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    assert "q_bsn" not in response.text
    assert "123" not in response.text.split("<form", 1)[0]


async def test_the_widget_describes_the_custom_fields(site: httpx.AsyncClient) -> None:
    body = (await site.get(f"{WIDGET}/", headers={"origin": SITE})).json()

    by_name = {f["name"]: f for f in body["fields"]}
    assert list(by_name) == [
        "first_name",
        "last_name",
        "email",
        "phone",
        "q_customer_no",
        "q_topic",
        "q_newsletter",
    ]
    assert by_name["q_topic"]["kind"] == "radio"
    assert by_name["q_topic"]["options"] == [
        {"value": "rent", "label": "Huur"},
        {"value": "care", "label": "Zorg"},
    ]
    assert by_name["q_newsletter"]["kind"] == "checkbox"
    assert "error.invalid_answer" in body["texts"]


async def _widget_book(site: httpx.AsyncClient, **body: object) -> httpx.Response:
    return await site.post(
        f"{WIDGET}/bookings",
        json={"start": FRIDAY_0900, "email": "ada@example.org", **body},
        headers={"origin": SITE},
    )


async def test_the_widget_books_with_answers(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    response = await _widget_book(
        site,
        first_name="Ada",
        last_name="Lovelace",
        answers={"customer_no": "A-1", "topic": "care", "newsletter": "on"},
    )

    assert response.status_code == 201
    (stored,) = await stored_answers(demo_settings)
    assert json.loads(stored or "{}")["answers"] == {
        "customer_no": "A-1",
        "topic": "care",
        "newsletter": True,
    }


async def test_a_refused_widget_answer_names_its_input(site: httpx.AsyncClient) -> None:
    response = await _widget_book(
        site, first_name="Ada", last_name="Lovelace", answers={"topic": "care"}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_answer", "field": "q_customer_no"}
    unknown = await _widget_book(
        site, first_name="Ada", last_name="L", answers={"customer_no": "A", "x_y": "1"}
    )
    assert unknown.json() == {"error": "invalid_answer", "field": None}


async def test_the_deprecated_name_is_split(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    response = await _widget_book(site, name="Ada Augusta Lovelace", answers={"customer_no": "A-1"})

    assert response.status_code == 201
    engine = create_engine(demo_settings.database_url)
    async with engine.connect() as conn:
        names = (await conn.execute(select(BookingRow.first_name, BookingRow.last_name))).one()
    await engine.dispose()
    assert tuple(names) == ("Ada", "Augusta Lovelace")


async def test_oversized_answers_are_refused_before_the_domain(site: httpx.AsyncClient) -> None:
    too_long = await _widget_book(
        site, first_name="Ada", last_name="L", answers={"customer_no": "x" * 1002}
    )
    too_many = await _widget_book(
        site, first_name="Ada", last_name="L", answers={f"k{i}": "x" for i in range(16)}
    )

    assert (too_long.status_code, too_many.status_code) == (422, 422)
    assert too_long.json()["error"] == "invalid_request"
