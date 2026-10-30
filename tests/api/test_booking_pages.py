"""End-to-end tests of the citizen pages against a migrated database with the demo group.

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; the demo group is open on weekdays
09:00-12:00 and 13:00-17:00 Amsterdam time, two places per slot.
"""

import re
from collections.abc import AsyncIterator
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import BookingRow, GroupRow, ScheduleRuleRow
from app.config import Settings
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW

FRIDAY_0900 = "2026-01-02T08:00:00Z"  # 09:00 Amsterdam
FRIDAY_0930 = "2026-01-02T08:30:00Z"
SAME_ORIGIN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
ADA = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.org", "phone": ""}


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


async def test_group_page_shows_the_month_and_the_first_free_day(
    site: httpx.AsyncClient,
) -> None:
    response = await site.get("/g/demo")

    assert response.status_code == 200
    text = response.text
    assert "<h1>Demo: videogesprek</h1>" in text
    assert '<h2 id="month-heading">januari 2026</h2>' in text
    # Today 09:00 local + 60 minutes notice: the first bookable slot today is 10:00.
    assert "<strong>Eerst mogelijk:</strong> donderdag 1 januari, 10:00" in text
    assert 'value="2026-01-01T08:30:00Z"' not in text
    assert 'value="2026-01-01T09:00:00Z"' in text
    assert '<h2 id="times-heading" tabindex="-1">donderdag 1 januari</h2>' in text
    assert "<legend>Ochtend</legend>" in text
    assert "<legend>Middag</legend>" in text
    # Free days are links with a full label; weekends are not.
    assert 'href="/g/demo?day=2026-01-02#times"' in text
    assert "vrijdag 2 januari, tijden vrij" in text
    assert "zaterdag 3 januari, geen tijden vrij" in text
    assert 'href="/g/demo?day=2026-01-03' not in text
    assert 'aria-current="true"' in text  # the day whose times are shown
    assert "Vorige maand" not in text  # current month
    assert 'href="/g/demo?month=2026-02"' in text
    assert '<script src="/static/calendar.js" defer>' in text
    assert "script-src 'self'" in response.headers["content-security-policy"]


async def test_a_day_link_shows_that_days_times(site: httpx.AsyncClient) -> None:
    text = (await site.get("/g/demo", params={"day": "2026-01-02"})).text

    assert '<h2 id="times-heading" tabindex="-1">vrijdag 2 januari</h2>' in text
    assert f'name="start" value="{FRIDAY_0900}"' in text
    assert "14 tijden vrij" in text
    assert 'class="time-button" name="start" value="2026-01-01T09:00:00Z"' not in text


async def test_month_navigation_is_clamped_and_ignores_garbage(site: httpx.AsyncClient) -> None:
    past = await site.get("/g/demo", params={"month": "2020-01"})
    garbage = await site.get("/g/demo", params={"month": "not-a-month", "day": "2026-99-01"})
    future = await site.get("/g/demo", params={"month": "2030-01"})
    closed_day = await site.get("/g/demo", params={"day": "2026-01-03"})

    assert "januari 2026</h2>" in past.text
    assert "januari 2026</h2>" in garbage.text
    assert "maart 2026</h2>" in future.text  # horizon: 60 days
    assert "Volgende maand" not in future.text
    assert "Vorige maand" in future.text
    # A day without times falls back to the first free day of its month.
    assert '<h2 id="times-heading" tabindex="-1">donderdag 1 januari</h2>' in closed_day.text


async def test_first_visit_skips_months_without_free_slots(demo_settings: Settings) -> None:
    new_years_eve = FixedClock(NOW - timedelta(days=1, hours=-10))  # Wed 31 Dec 18:00 UTC

    async with app_client(demo_settings, new_years_eve) as client:
        first_visit = await client.get("/g/demo")
        december = await client.get("/g/demo", params={"month": "2025-12"})

    assert "januari 2026</h2>" in first_visit.text
    assert "Vorige maand" in first_visit.text
    assert "Geen tijden in deze maand" in december.text


async def test_group_page_in_english(site: httpx.AsyncClient) -> None:
    text = (await site.get("/g/demo", params={"lang": "en", "day": "2026-01-02"})).text

    assert "January 2026</h2>" in text
    assert "<legend>Morning</legend>" in text
    assert "Choose this time" in text
    assert 'href="?month=' not in text  # the language switcher keeps only known parameters
    assert "?day=2026-01-02&amp;lang=nl" in text


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
    # The management page shows no contact details. Check the whole surname: a short
    # substring such as "Ada" can occur by chance in the random token in the page's links.
    assert "Lovelace" not in page.text
    assert "example.org" not in page.text


async def test_invalid_input_is_explained_and_kept(site: httpx.AsyncClient) -> None:
    response = await book(site, email="not-an-email")

    assert response.status_code == 422
    text = response.text
    assert 'role="alert"' in text
    assert '<a href="#email">Vul een geldig e-mailadres in' in text
    assert 'aria-invalid="true"' in text
    assert 'aria-describedby="email-hint email-error"' in text
    assert 'value="Lovelace"' in text


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
    friday = await site.get("/g/demo", params={"day": "2026-01-02"})
    assert friday.text.count(f'value="{FRIDAY_0900}"') == 1


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

    picker = await site.get(f"/b/{old}/reschedule", params={"day": "2026-01-02"})
    confirm = await site.get(f"/b/{old}/reschedule", params={"start": FRIDAY_0930})
    response = await site.post(
        f"/b/{old}/reschedule", data={"start": FRIDAY_0930}, headers=SAME_ORIGIN
    )

    assert "Uw huidige afspraak: vrijdag 2 januari 2026, 09:00–09:30." in picker.text
    assert f'value="{FRIDAY_0900}"' not in picker.text  # the current slot is not offered
    assert f'value="{FRIDAY_0930}"' in picker.text
    assert f'action="/b/{old}/reschedule"' in picker.text
    # A chosen time is confirmed first (from → to), so one click never moves a booking.
    assert "Nieuw moment bevestigen" in confirm.text
    assert "vrijdag 2 januari 2026, 09:30–10:00" in confirm.text
    assert f'<form method="post" action="/b/{old}/reschedule">' in confirm.text
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


async def test_reschedule_confirmation_needs_a_free_other_time(site: httpx.AsyncClient) -> None:
    token = token_of(await book(site))

    same = await site.get(f"/b/{token}/reschedule", params={"start": FRIDAY_0900})
    garbage = await site.get(f"/b/{token}/reschedule", params={"start": "2026-01-02T08:07:00Z"})

    for response in (same, garbage):
        assert "Nieuw moment bevestigen" not in response.text
        assert 'id="month-heading"' in response.text  # back on the picker


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


async def test_days_after_the_horizon_are_not_yet_bookable(site: httpx.AsyncClient) -> None:
    # Horizon 60 days from Thursday 1 January: bookable up to and including 2 March.
    text = (await site.get("/g/demo", params={"month": "2026-03"})).text

    assert 'href="/g/demo?day=2026-03-02#times"' in text
    assert "maandag 2 maart, tijden vrij" in text
    assert "dinsdag 3 maart, nog niet te boeken" in text
    assert "zondag 1 maart, geen tijden vrij" in text  # closed, not beyond the horizon
    assert 'calendar__day--later" data-day="2026-03-03"' in text
    assert "U kunt tot 60 dagen vooruit boeken." in text


async def test_first_free_moment_is_found_beyond_two_months(demo_settings: Settings) -> None:
    engine = create_engine(demo_settings.database_url)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(update(GroupRow).values(horizon_days=150))
        await session.execute(update(ScheduleRuleRow).values(valid_from=date(2026, 4, 1)))
    await engine.dispose()

    async with app_client(demo_settings, FixedClock(NOW)) as client:
        text = (await client.get("/g/demo")).text

    assert "<strong>Eerst mogelijk:</strong> woensdag 1 april, 09:00" in text
    assert '<h2 id="month-heading">april 2026</h2>' in text
    assert '<h2 id="times-heading" tabindex="-1">woensdag 1 april</h2>' in text


async def test_filter_finds_the_first_slot_that_fits(site: httpx.AsyncClient) -> None:
    # "Friday from 13:00": the first fitting slot is Friday 2 January 13:00.
    text = (await site.get("/g/demo", params=[("days", "4"), ("from", "13")])).text

    assert "<strong>Eerst passend:</strong> vrijdag 2 januari, 13:00" in text
    assert '<details class="filter" open>' in text
    assert '<input type="checkbox" name="days" value="4" id="days-4" checked>' in text
    assert '<option value="13" selected>13:00</option>' in text
    # Only Fridays are free days now, and links keep the filter.
    assert 'href="/g/demo?days=4&amp;from=13&amp;day=2026-01-09#times"' in text
    assert 'href="/g/demo?days=4&amp;from=13&amp;day=2026-01-08' not in text
    assert "donderdag 8 januari, geen tijden vrij" in text
    assert 'href="/g/demo?days=4&amp;from=13&amp;month=2026-02"' in text
    assert '<h2 id="times-heading" tabindex="-1">vrijdag 2 januari</h2>' in text
    assert "<legend>Ochtend</legend>" not in text
    assert "8 tijden vrij" in text
    assert 'href="/g/demo">Filter wissen</a>' in text


async def test_filter_until_keeps_slots_that_end_in_time(site: httpx.AsyncClient) -> None:
    text = (
        await site.get("/g/demo", params=[("day", "2026-01-02"), ("from", "9"), ("until", "10")])
    ).text

    assert f'name="start" value="{FRIDAY_0900}"' in text
    assert f'name="start" value="{FRIDAY_0930}"' in text
    assert 'value="2026-01-02T09:00:00Z"' not in text  # 10:00 ends at 10:30
    assert "2 tijden vrij" in text


async def test_filter_without_a_fit_says_so(site: httpx.AsyncClient) -> None:
    text = (await site.get("/g/demo", params=[("days", "5"), ("days", "6")])).text

    assert "Geen tijden die passen" in text
    assert "Eerst passend" not in text
    assert 'id="times-heading"' in text


async def test_filter_rejects_an_end_before_the_start(site: httpx.AsyncClient) -> None:
    response = await site.get("/g/demo", params=[("from", "15"), ("until", "10")])

    assert response.status_code == 200
    assert 'id="filter-until-error"' in response.text
    assert "De eindtijd moet na de begintijd liggen." in response.text
    assert 'aria-describedby="filter-until-error"' in response.text


async def test_filter_ignores_garbage_and_the_switcher_keeps_it(site: httpx.AsyncClient) -> None:
    garbage = await site.get(
        "/g/demo", params=[("days", "9"), ("days", "x"), ("from", "abc"), ("until", "99")]
    )
    kept = (await site.get("/g/demo", params=[("days", "1"), ("days", "4"), ("from", "13")])).text

    assert garbage.status_code == 200
    assert "<strong>Eerst mogelijk:</strong>" in garbage.text
    assert '<details class="filter">' in garbage.text
    assert "?days=1&amp;days=4&amp;from=13&amp;lang=en" in kept


async def test_a_booking_awaiting_a_new_time_offers_the_choice_after_its_time(
    demo_settings: Settings,
) -> None:
    async with app_client(demo_settings, FixedClock(NOW)) as early:
        token = token_of(await book(early))
    engine = create_engine(demo_settings.database_url)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            update(BookingRow).values(
                status="awaiting_rebook",
                change_reason="staffing",
                rebook_by=NOW + timedelta(days=15),
            )
        )
    await engine.dispose()
    late_clock = FixedClock(NOW + timedelta(days=2))  # past the original time

    async with app_client(demo_settings, late_clock) as late:
        page = (await late.get(f"/b/{token}")).text
        picker = await late.get(f"/b/{token}/reschedule")

    assert "Wacht op uw nieuwe tijd" in page
    assert "Kiest u voor vrijdag 16 januari 2026, 09:00 geen nieuwe tijd" in page
    assert f'href="/b/{token}/reschedule"' in page
    assert picker.status_code == 200
