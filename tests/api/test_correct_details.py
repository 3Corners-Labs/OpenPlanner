"""The participant corrects name, phone and answers on the management page (ADR 0056 point 10).

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; Ada books the demo group on Friday 09:00
Amsterdam; the change deadline is 60 minutes before the start.
"""

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow
from app.config import Settings
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.participant import ADA, SAME_ORIGIN, book, booked, emailed_token, open_link


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


async def _stored_first_name(client: httpx.AsyncClient) -> str:
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        row = await session.scalar(select(BookingRow).order_by(BookingRow.id.desc()).limit(1))
    assert row is not None
    return str(row.first_name)


async def test_the_details_are_corrected_and_the_email_stays(site: httpx.AsyncClient) -> None:
    base = await open_link(site, await booked(site))

    page = await site.get(base)
    form = await site.get(f"{base}/details")
    response = await site.post(
        f"{base}/details",
        data={**ADA, "first_name": "Augusta", "email": "other@example.org"},
        headers=SAME_ORIGIN,
    )

    assert f'href="{base}/details"' in page.text
    assert form.status_code == 200
    assert form.headers["cache-control"] == "no-store"
    assert 'value="Ada"' in form.text
    assert "ada@example.org" in form.text
    assert 'name="email"' not in form.text
    assert response.status_code == 303
    assert response.headers["location"] == f"{base}?status=details"
    assert "Uw gegevens zijn aangepast" in (await site.get(response.headers["location"])).text
    assert await _stored_first_name(site) == "Augusta"
    assert "other@example.org" not in (await site.get(f"{base}/details")).text


async def test_an_invalid_correction_shows_the_form_again(site: httpx.AsyncClient) -> None:
    base = await open_link(site, await booked(site))

    response = await site.post(
        f"{base}/details", data={**ADA, "first_name": ""}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert 'aria-invalid="true"' in response.text
    assert await _stored_first_name(site) == "Ada"


async def test_a_booking_awaiting_confirmation_can_be_corrected(site: httpx.AsyncClient) -> None:
    await book(site)
    base = await open_link(site, await emailed_token(site))

    response = await site.post(
        f"{base}/details", data={**ADA, "last_name": "Byron"}, headers=SAME_ORIGIN
    )

    assert response.headers["location"] == f"{base}?status=details"


async def test_after_the_change_deadline_the_details_are_closed(demo_settings: Settings) -> None:
    async with app_client(demo_settings, FixedClock(NOW)) as early:
        token = await booked(early)
    late_clock = FixedClock(NOW + timedelta(days=1) - timedelta(minutes=5))  # Fri 07:55 UTC

    async with app_client(demo_settings, late_clock) as late:
        base = await open_link(late, token)
        page = await late.get(base)
        form = await late.get(f"{base}/details")
        post = await late.post(
            f"{base}/details", data={**ADA, "first_name": "Augusta"}, headers=SAME_ORIGIN
        )
        stored = await _stored_first_name(late)

    assert f'href="{base}/details"' not in page.text
    assert form.status_code == 303
    assert post.status_code == 303
    assert post.headers["location"] == base
    assert stored == "Ada"


async def test_the_honeypot_refuses_a_correction(site: httpx.AsyncClient) -> None:
    base = await open_link(site, await booked(site))

    response = await site.post(
        f"{base}/details", data={**ADA, "website": "spam"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 400


async def test_a_cross_site_correction_is_refused(site: httpx.AsyncClient) -> None:
    base = await open_link(site, await booked(site))

    response = await site.post(
        f"{base}/details",
        data={**ADA, "first_name": "Eve"},
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert await _stored_first_name(site) == "Ada"


async def test_an_unknown_booking_has_no_details_page(site: httpx.AsyncClient) -> None:
    assert (await site.get("/b/m/unknown/details")).status_code == 403  # no link session
