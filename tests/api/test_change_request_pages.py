"""The participant confirms a bot's change request (ADR 0023 step 2) on the real app.

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; Ada booked Friday 09:00 with phone number
06 1234 5678 in the demo group, whose organisation takes change requests.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.change_request_service import ChangeRequestService
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.agent_api import registered_caller
from tests.support.domain import NOW
from tests.support.participant import SAME_ORIGIN, booked, open_link

FRIDAY_0930 = datetime(2026, 1, 2, 8, 30, tzinfo=UTC)


@pytest.fixture
def on(demo_settings: Settings) -> Settings:
    return demo_settings.model_copy(
        update={"agent_api_enabled": True, "agent_change_requests_enabled": True}
    )


@pytest.fixture
async def site(on: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(on, FixedClock(NOW)) as client:
        yield client


def _app(client: httpx.AsyncClient) -> Any:
    return client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001


async def _ask(client: httpx.AsyncClient, base: str, start: datetime | None = None) -> None:
    """Have the bot ask for Ada's booking, with the code the booking page shows."""
    page = await client.get(base)
    code = page.text.split("Afspraakcode</dt><dd>")[1].split("</dd>")[0]
    service: ChangeRequestService = _app(client).state.change_request_service
    async with SqlUnitOfWork(_app(client).state.engine).read() as tx:
        demo = await tx.get_group_summary("demo")
    assert demo is not None
    caller = await registered_caller(_app(client))
    request = await service.request(caller, demo, "06 1234 5678", code, start)
    assert request.booking_id is not None


async def test_the_booking_page_shows_the_code_only_when_switched_on(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    token = await booked(site, phone="0612345678")

    shown = await site.get(await open_link(site, token))
    async with app_client(demo_settings, FixedClock(NOW)) as plain:
        hidden = await plain.get(await open_link(plain, token))

    assert "Afspraakcode" in shown.text
    assert "Afspraakcode" not in hidden.text


async def test_confirming_a_cancellation(site: httpx.AsyncClient) -> None:
    base = await open_link(site, await booked(site, phone="0612345678"))
    await _ask(site, base)

    page = await site.get(f"{base}/change")
    response = await site.post(f"{base}/change", headers=SAME_ORIGIN)

    assert page.status_code == 200
    assert "Ja, annuleer mijn afspraak" in page.text
    assert f'action="{base}/change"' in page.text
    assert response.headers["location"] == f"{base}?status=cancelled"
    again = await site.get(f"{base}/change")
    assert again.headers["location"] == f"{base}?status=change_gone"


async def test_confirming_a_new_time_moves_the_booking(site: httpx.AsyncClient) -> None:
    base = await open_link(site, await booked(site, phone="0612345678"))
    await _ask(site, base, FRIDAY_0930)

    page = await site.get(f"{base}/change")
    response = await site.post(f"{base}/change", headers=SAME_ORIGIN)

    assert "Ja, verplaats mijn afspraak" in page.text
    assert "09:30" in page.text
    target = response.headers["location"]
    assert target.endswith("?status=rescheduled")
    assert not target.startswith(base)


async def test_an_expired_request_changes_nothing(on: Settings) -> None:
    async with app_client(on, FixedClock(NOW)) as early:
        token = await booked(early, phone="0612345678")
        await _ask(early, await open_link(early, token))

    async with app_client(on, FixedClock(NOW + timedelta(minutes=60))) as client:
        base = await open_link(client, token)
        page = await client.get(f"{base}/change")
        response = await client.post(f"{base}/change", headers=SAME_ORIGIN)
        booking = await client.get(response.headers["location"])

    assert page.headers["location"] == f"{base}?status=change_gone"
    assert response.headers["location"] == f"{base}?status=change_gone"
    assert "Uw afspraak is niet veranderd" in booking.text


async def test_a_cross_site_confirmation_is_refused(site: httpx.AsyncClient) -> None:
    base = await open_link(site, await booked(site, phone="0612345678"))
    await _ask(site, base)

    response = await site.post(f"{base}/change", headers={"origin": "https://evil.example"})

    assert response.status_code == 403
