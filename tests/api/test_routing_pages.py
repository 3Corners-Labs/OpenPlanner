"""Subjects and routing questions on the participant pages and in the widget (ADR 0081).

The demo group gets two subjects and the preferred-language question; its blocks offer only
``move`` where a test says so. Calendar as in ``test_booking_pages``.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import BookingRow, GroupRow, ScheduleRuleRow
from app.adapters.db.routing_json import routing_json
from app.config import Settings
from app.domain.routing import Attribute, GroupRouting, Subject
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.widget import SITE, embed

FRIDAY_0900 = "2026-01-02T08:00:00Z"
SAME_ORIGIN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
ADA = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.org", "phone": ""}
WIDGET = "/api/v1/widget/demo/demo"
ROUTING = GroupRouting(
    (
        Subject.create("general", {"nl": "Algemene vraag", "en": "General question"}),
        Subject.create("move", {"nl": "Verhuizing", "en": "Moving"}),
    ),
    (Attribute.PREFERRED_LANGUAGE,),
)


async def _with_routing(settings: Settings, *, bind: bool = False) -> None:
    engine = create_engine(settings.database_url)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            update(GroupRow).where(GroupRow.slug == "demo").values(routing=routing_json(ROUTING))
        )
        if bind:
            demo = select(GroupRow.id).where(GroupRow.slug == "demo").scalar_subquery()
            await session.execute(
                update(ScheduleRuleRow)
                .where(ScheduleRuleRow.group_id == demo)
                .values(subjects="move")
            )
    await engine.dispose()


async def _stored(settings: Settings) -> list[tuple[str | None, str | None]]:
    engine = create_engine(settings.database_url)
    async with engine.connect() as conn:
        rows = await conn.execute(select(BookingRow.subject_code, BookingRow.routing_answers))
        found = [(a, b) for a, b in rows]
    await engine.dispose()
    return found


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    await _with_routing(demo_settings)
    await embed(demo_settings)
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


@pytest.fixture
async def bound(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    await _with_routing(demo_settings, bind=True)
    await embed(demo_settings)
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


async def test_the_subject_is_the_first_question_then_the_routing_questions(
    site: httpx.AsyncClient,
) -> None:
    text = (await site.get(f"/g/demo/book?start={FRIDAY_0900}")).text
    assert (
        text.index('name="q__subject"')
        < text.index('name="q__preferred_language"')
        < text.index('name="first_name"')
    )
    assert "Waarover gaat uw afspraak?" in text
    assert '<label for="q__subject-2">Verhuizing</label>' in text
    assert '<label for="q__preferred_language-2">Engels</label>' in text


async def test_a_subject_chosen_before_the_calendar_is_preselected(
    site: httpx.AsyncClient,
) -> None:
    page = (await site.get("/g/demo?subject=move")).text
    assert 'name="subject" type="radio" value="move" checked' in page
    assert '<input type="hidden" name="subject" value="move">' in page
    form = (await site.get(f"/g/demo/book?start={FRIDAY_0900}&subject=move")).text
    assert 'name="q__subject" type="radio" value="move" checked' in form


async def test_the_codes_are_stored_with_the_booking(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    response = await site.post(
        "/g/demo/book",
        data={"start": FRIDAY_0900, **ADA, "q__subject": "move", "q__preferred_language": "en"},
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 303
    assert await _stored(demo_settings) == [("move", '{"preferred_language": "en"}')]


async def test_a_crafted_subject_is_refused_and_linked_to_the_question(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    response = await site.post(
        "/g/demo/book",
        data={
            "start": FRIDAY_0900,
            **ADA,
            "q__subject": "<script>",
            "q__preferred_language": "en",
        },
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 422
    assert '<a href="#q__subject">Controleer uw antwoord bij ‘Waarover gaat uw afspraak?’.' in (
        response.text
    )
    assert "&lt;script&gt;" not in response.text.split("<main", 1)[-1].split("q__subject", 1)[0]
    assert await _stored(demo_settings) == []


async def test_blocks_that_do_not_offer_the_subject_show_no_times(
    bound: httpx.AsyncClient,
) -> None:
    general = (await bound.get("/g/demo?subject=general")).text
    move = (await bound.get("/g/demo?subject=move")).text
    assert 'name="start"' not in general
    assert 'name="start"' in move
    response = await bound.post(
        "/g/demo/book",
        data={"start": FRIDAY_0900, **ADA, "q__subject": "general", "q__preferred_language": "nl"},
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 409


async def test_the_widget_asks_the_routing_questions_and_filters_by_subject(
    bound: httpx.AsyncClient, demo_settings: Settings
) -> None:
    group = (await bound.get(f"{WIDGET}/", headers={"origin": SITE})).json()
    names = [f["name"] for f in group["fields"]]
    assert names[:3] == ["q__subject", "q__preferred_language", "first_name"]
    general = (
        await bound.get(f"{WIDGET}/availability?subject=general", headers={"origin": SITE})
    ).json()
    move = (await bound.get(f"{WIDGET}/availability?subject=move", headers={"origin": SITE})).json()
    assert general["times"] is None or not general["times"]["groups"]
    assert move["times"]["groups"]
    response = await bound.post(
        f"{WIDGET}/bookings",
        json={
            "start": FRIDAY_0900,
            "email": "ada@example.org",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "answers": {"_subject": "move", "_preferred_language": "nl"},
        },
        headers={"origin": SITE},
    )
    assert response.status_code == 201
    assert await _stored(demo_settings) == [("move", '{"preferred_language": "nl"}')]
