"""What citizens see of an emergency closure (ADR 0037 §5, CL-2).

The seeded examples: ``demo-gesloten`` closed since yesterday until further notice, with a
heading and three contact options and a booking on hold next week; ``demo-gepland`` closed in
two weeks. "Now" is Thursday 1 January 2026 08:00 UTC.
"""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import ClosureExamples, seed_closure_examples
from app.config import Settings
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW

CLOSED = "/g/demo-gesloten"
PLANNED = "/g/demo-gepland"
PHONE_LINK = '<a class="contact-block__link" href="tel:14020">14 020</a>'


@pytest.fixture
async def examples(demo_settings: Settings) -> ClosureExamples:
    engine = create_engine(demo_settings.database_url)
    key = demo_settings.management_token_key
    assert key is not None
    try:
        return await seed_closure_examples(engine, NOW, key.get_secret_value().encode())
    finally:
        await engine.dispose()


@pytest.fixture
async def site(
    demo_settings: Settings, examples: ClosureExamples
) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


async def test_a_closed_team_shows_the_closure_page_instead_of_the_calendar(
    site: httpx.AsyncClient,
) -> None:
    page = (await site.get(CLOSED)).text

    assert '<h1 id="closure-heading">Storing bij het loket</h1>' in page
    assert "Gesloten vanaf woensdag 31 december 2025, 08:00, tot nader bericht." in page
    assert "<p>Wij werken aan een oplossing.</p>" in page
    assert '<h2 id="closure-contacts-heading" class="contact-block__heading">' in page
    assert PHONE_LINK in page
    assert 'href="https://www.example.org/contact"' in page
    assert "calendar.js" not in page  # no calendar, no steps, no direct call
    assert 'class="steps"' not in page
    assert "/direct" not in page
    # Heading, period, notice, contact options: in that order.
    assert (
        page.index("Storing bij het loket")
        < page.index("tot nader bericht")
        < page.index("Wij werken aan een oplossing")
        < page.index(PHONE_LINK)
    )


async def test_the_closure_page_is_in_the_readers_language(site: httpx.AsyncClient) -> None:
    page = (await site.get(CLOSED, params={"lang": "en"})).text

    assert '<h1 id="closure-heading">Outage at the desk</h1>' in page
    assert "We are working on a solution." in page
    assert "Call us" in page
    assert "Weekdays, 9:00 to 17:00" in page


async def test_an_upcoming_closure_keeps_the_calendar_and_links_to_its_page(
    site: httpx.AsyncClient,
) -> None:
    page = (await site.get(PLANNED)).text

    assert "Let op: binnenkort tijdelijk gesloten" in page
    assert "<p><strong>Storing bij het loket</strong></p>" in page
    assert f'<a href="{PLANNED}/closure">' in page
    assert "calendar.js" in page  # still bookable before the start
    assert PHONE_LINK not in page  # the options are on the closure page


async def test_the_page_of_an_upcoming_closure_says_when_and_leads_back(
    site: httpx.AsyncClient,
) -> None:
    page = (await site.get(f"{PLANNED}/closure")).text

    assert '<h1 id="closure-heading">Storing bij het loket</h1>' in page
    assert "Let op: binnenkort tijdelijk gesloten" in page
    assert (
        "Gesloten van donderdag 15 januari 2026, 08:00 tot zaterdag 17 januari 2026, 08:00." in page
    )
    assert PHONE_LINK in page
    assert f'href="{PLANNED}"' in page  # back to the calendar, which still works


async def test_the_closure_page_of_an_open_team_goes_back_to_the_calendar(
    site: httpx.AsyncClient,
) -> None:
    response = await site.get("/g/demo/closure")

    assert response.status_code == 303
    assert response.headers["location"] == "/g/demo"
    assert (await site.get("/g/onbekend/closure")).status_code == 404


async def test_a_booking_on_hold_shows_the_contact_options_above_its_actions(
    site: httpx.AsyncClient, examples: ClosureExamples
) -> None:
    page = (await site.get(examples.suspended_booking)).text

    assert "Uw afspraak kan niet doorgaan op dit moment" in page
    assert "<p><strong>Storing bij het loket</strong></p>" in page
    assert PHONE_LINK in page
    assert page.index(PHONE_LINK) < page.index(f'href="{examples.suspended_booking}/reschedule"')
    assert "inwoner@example.org" not in page  # the page never shows contact details
