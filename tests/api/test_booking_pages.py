"""End-to-end tests of the citizen pages against a migrated database with the demo group.

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; the demo group is open on weekdays
09:00-12:00 and 13:00-17:00 Amsterdam time, two places per slot.
"""

import re
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.config import Settings
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.database import migrated_database
from tests.support.domain import NOW

FRIDAY_0900 = "2026-01-02T08:00:00Z"  # 09:00 Amsterdam
FRIDAY_0930 = "2026-01-02T08:30:00Z"
SAME_ORIGIN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
ADA = {"name": "Ada Lovelace", "email": "ada@example.org", "phone": ""}


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    # Synchronous: Alembic runs its own event loop.
    return migrated_database(tmp_path, monkeypatch, "pages")


@pytest.fixture
async def demo_settings(settings: Settings, database_url: str) -> Settings:
    url = database_url
    engine = create_engine(url)
    await seed_demo(engine)
    await engine.dispose()
    return settings.model_copy(update={"database_url": url})


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


async def book(site: httpx.AsyncClient, start: str = FRIDAY_0900, **fields: str) -> httpx.Response:
    return await site.post(
        "/g/demo/book", data={"start": start, **ADA, **fields}, headers=SAME_ORIGIN
    )


def token_of(response: httpx.Response) -> str:
    match = re.fullmatch(r"/b/([A-Za-z0-9_-]+)\?status=\w+", response.headers["location"])
    assert match
    return match.group(1)


async def test_group_page_lists_slots_per_day(site: httpx.AsyncClient) -> None:
    response = await site.get("/g/demo")

    assert response.status_code == 200
    text = response.text
    assert "<h1>Demo: videogesprek</h1>" in text
    assert "<legend>vrijdag 2 januari</legend>" in text
    assert f'value="{FRIDAY_0900}"' in text
    # Today 09:00 local + 60 minutes notice: the first bookable slot today is 10:00.
    assert 'value="2026-01-01T08:30:00Z"' not in text
    assert 'value="2026-01-01T09:00:00Z"' in text
    assert "Vorige week" not in text  # current week
    assert "?week=2026-01-05" in text


async def test_week_navigation_is_clamped(site: httpx.AsyncClient) -> None:
    past = await site.get("/g/demo", params={"week": "2020-01-01"})
    garbage = await site.get("/g/demo", params={"week": "not-a-date"})
    future = await site.get("/g/demo", params={"week": "2030-01-01"})

    assert "Week van maandag 29 december" in past.text
    assert "Week van maandag 29 december" in garbage.text
    assert "Volgende week" not in future.text
    assert "Vorige week" in future.text


async def test_first_visit_skips_weeks_without_free_slots(demo_settings: Settings) -> None:
    friday_evening = FixedClock(NOW + timedelta(days=1, hours=10))  # Fri 2 Jan 18:00 UTC

    async with app_client(demo_settings, friday_evening) as client:
        first_visit = await client.get("/g/demo")
        chosen_week = await client.get("/g/demo", params={"week": "2026-01-02"})

    assert "Week van maandag 5 januari" in first_visit.text
    assert "Vorige week" in first_visit.text
    assert "geen tijden beschikbaar" in chosen_week.text


async def test_group_page_in_english(site: httpx.AsyncClient) -> None:
    text = (await site.get("/g/demo", params={"lang": "en"})).text

    assert (
        "<legend>Friday 2 January</legend>" in text or "<legend>Friday January 2</legend>" in text
    )
    assert "Continue" in text


async def test_unknown_group_is_a_404_page(site: httpx.AsyncClient) -> None:
    response = await site.get("/g/nope")

    assert response.status_code == 404
    assert "Pagina niet gevonden" in response.text


async def test_booking_form_shows_the_slot_and_fields(site: httpx.AsyncClient) -> None:
    response = await site.get("/g/demo/book", params={"start": FRIDAY_0900})

    text = response.text
    assert "vrijdag 2 januari 2026, 09:00–09:30" in text
    assert 'autocomplete="email"' in text
    assert 'id="phone"' in text
    assert "(optioneel)" in text
    assert 'aria-describedby="email-hint"' in text
    assert 'name="website"' in text


@pytest.mark.parametrize("start", ["", "garbage", "2026-01-02T08:07:00Z", "2026-01-02T08:00:00"])
async def test_booking_form_for_an_unavailable_slot_goes_back(
    site: httpx.AsyncClient, start: str
) -> None:
    response = await site.get("/g/demo/book", params={"start": start})

    assert response.status_code == 303
    assert response.headers["location"] == "/g/demo?status=unavailable"
    back = await site.get(response.headers["location"])
    assert 'role="alert"' in back.text
    assert "niet meer beschikbaar" in back.text


async def test_full_booking_flow(site: httpx.AsyncClient) -> None:
    response = await book(site)

    assert response.status_code == 303
    token = token_of(response)
    page = await site.get(response.headers["location"])
    assert "Uw afspraak is gemaakt" in page.text
    assert "vrijdag 2 januari 2026, 09:00–09:30" in page.text
    assert "Bevestigd" in page.text
    assert f'href="/b/{token}/reschedule"' in page.text
    assert "Ada" not in page.text  # the management page shows no contact details
    assert "example.org" not in page.text


async def test_invalid_input_is_explained_and_kept(site: httpx.AsyncClient) -> None:
    response = await book(site, email="not-an-email")

    assert response.status_code == 422
    text = response.text
    assert 'role="alert"' in text
    assert '<a href="#email">Vul een geldig e-mailadres in' in text
    assert 'aria-invalid="true"' in text
    assert 'aria-describedby="email-hint email-error"' in text
    assert 'value="Ada Lovelace"' in text


async def test_full_slot_is_reported(site: httpx.AsyncClient) -> None:
    await book(site)
    await book(site)

    response = await book(site)

    assert response.status_code == 409
    assert "net vol geraakt" in response.text


async def test_honeypot_is_refused(site: httpx.AsyncClient) -> None:
    response = await book(site, website="https://spam.example")

    assert response.status_code == 400
    assert "kon niet worden verwerkt" in response.text
    assert "spam.example" not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://evil.example"},
        {"origin": "null"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
        {"sec-fetch-site": "cross-site", "origin": "http://testserver"},
    ],
)
async def test_cross_site_posts_are_refused(
    site: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await site.post("/g/demo/book", data={"start": FRIDAY_0900, **ADA}, headers=headers)

    assert response.status_code == 403
    assert (await site.get("/g/demo")).text.count(f'value="{FRIDAY_0900}"') == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"sec-fetch-site": "same-origin", "origin": "null"},  # what real browsers can send
        {"sec-fetch-site": "none"},
        {"origin": "http://testserver"},  # older browser without Fetch Metadata
        {},  # non-browser client: no CSRF risk
    ],
)
async def test_same_origin_posts_are_accepted(
    site: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await site.post("/g/demo/book", data={"start": FRIDAY_0900, **ADA}, headers=headers)

    assert response.status_code == 303


async def test_cancel_flow(site: httpx.AsyncClient) -> None:
    token = token_of(await book(site))

    confirm = await site.get(f"/b/{token}/cancel")
    response = await site.post(f"/b/{token}/cancel", headers=SAME_ORIGIN)

    assert "Weet u zeker" in confirm.text
    assert response.status_code == 303
    page = await site.get(response.headers["location"])
    assert "Uw afspraak is geannuleerd" in page.text
    assert "Geannuleerd" in page.text
    assert "/reschedule" not in page.text
    assert (await site.get(f"/b/{token}/cancel")).status_code == 303


async def test_reschedule_flow(site: httpx.AsyncClient) -> None:
    old = token_of(await book(site))

    picker = await site.get(f"/b/{old}/reschedule")
    response = await site.post(
        f"/b/{old}/reschedule", data={"start": FRIDAY_0930}, headers=SAME_ORIGIN
    )

    assert "Uw huidige afspraak: vrijdag 2 januari 2026, 09:00–09:30." in picker.text
    assert f'value="{FRIDAY_0900}"' not in picker.text  # the current slot is not offered
    assert f'action="/b/{old}/reschedule"' in picker.text
    new = token_of(response)
    assert new != old
    assert "09:30–10:00" in (await site.get(f"/b/{new}")).text
    assert "Geannuleerd" in (await site.get(f"/b/{old}")).text


async def test_reschedule_errors_are_shown_on_the_picker(site: httpx.AsyncClient) -> None:
    token = token_of(await book(site))

    response = await site.post(
        f"/b/{token}/reschedule", data={"start": FRIDAY_0900}, headers=SAME_ORIGIN
    )

    assert response.status_code == 409
    assert "huidige afspraak. Kies een ander moment" in response.text


async def test_booking_is_read_only_from_the_dispatch_moment(demo_settings: Settings) -> None:
    async with app_client(demo_settings, FixedClock(NOW)) as early:
        token = token_of(await book(early))
    late_clock = FixedClock(NOW + timedelta(days=1) - timedelta(minutes=5))  # Fri 07:55 UTC

    async with app_client(demo_settings, late_clock) as late:
        page = await late.get(f"/b/{token}")
        cancel_page = await late.get(f"/b/{token}/cancel")
        cancel = await late.post(f"/b/{token}/cancel", headers=SAME_ORIGIN)
        reschedule_page = await late.get(f"/b/{token}/reschedule")

    assert "Deze afspraak staat vast" in page.text
    assert "/cancel" not in page.text
    assert cancel_page.status_code == 303
    assert reschedule_page.status_code == 303
    assert cancel.status_code == 409
    assert "kan niet meer worden gewijzigd" in cancel.text


async def test_unknown_token_is_a_404_page(site: httpx.AsyncClient) -> None:
    for path in ("/b/unknown", "/b/unknown/cancel", "/b/unknown/reschedule"):
        response = await site.get(path)
        assert response.status_code == 404


async def test_bookings_are_rate_limited(demo_settings: Settings) -> None:
    limited = demo_settings.model_copy(
        update={"rate_limit_enabled": True, "rate_limit_bookings": "2/hour"}
    )
    async with app_client(limited, FixedClock(NOW)) as client:
        first = await book(client, email="a@example.org")
        second = await book(client, start=FRIDAY_0930, email="b@example.org")
        third = await book(client, start=FRIDAY_0930, email="c@example.org")

    assert [first.status_code, second.status_code] == [303, 303]
    assert third.status_code == 429
    assert third.headers["retry-after"] == "60"
    assert "te vaak geprobeerd" in third.text


async def test_pages_carry_the_html_security_headers(site: httpx.AsyncClient) -> None:
    response = await site.get("/g/demo")

    assert "form-action 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
