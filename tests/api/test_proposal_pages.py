"""The participant answers a proposed new time (ADR 0057 point 8) on the real app.

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; the demo group is open on weekdays
09:00-12:00 and 13:00-17:00 Amsterdam time, two places per slot. Ada booked Friday 09:00; a
planner proposes Monday 5 January 09:00.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.booking import ChangeReason
from app.domain.booking_changes import ProposalTiming
from app.domain.reschedule_proposal import DEFAULT_PROPOSAL_HOURS
from tests.fakes.clock import FixedClock
from tests.support.app_client import app_client
from tests.support.domain import NOW
from tests.support.participant import SAME_ORIGIN, booked, open_link

MONDAY_0900 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
TIMING = ProposalTiming(rebook_days=14, rebook_reminder_days=3, hours=DEFAULT_PROPOSAL_HOURS)


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


async def _propose(client: httpx.AsyncClient, token: str) -> str:
    """Book page of Ada's booking after a planner proposed Monday 09:00 for it."""
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    base = await open_link(client, token)
    service = app.state.booking_service
    uow = SqlUnitOfWork(app.state.engine)
    async with uow.locked() as tx:
        details = await service.find(token)
        await service.propose_in(
            tx,
            details.booking,
            details.group,
            MONDAY_0900,
            reason=ChangeReason.STAFFING,
            timing=TIMING,
        )
    return base


async def test_the_booking_page_offers_the_proposed_time(site: httpx.AsyncClient) -> None:
    base = await _propose(site, await booked(site))

    page = await site.get(base)
    confirm = await site.get(f"{base}/proposal")

    assert "Er is een nieuwe tijd voor u voorgesteld" in page.text
    assert "maandag 5 januari 2026, 09:00" in page.text
    assert f'href="{base}/proposal"' in page.text
    assert f'href="{base}/reschedule"' in page.text
    assert confirm.status_code == 200
    assert f'action="{base}/proposal"' in confirm.text


async def test_confirming_moves_the_booking_to_a_new_reference(site: httpx.AsyncClient) -> None:
    base = await _propose(site, await booked(site))

    response = await site.post(f"{base}/proposal", headers=SAME_ORIGIN)

    assert response.status_code == 303
    target = response.headers["location"]
    assert target.endswith("?status=rescheduled")
    assert not target.startswith(base)
    page = await site.get(target)
    assert "maandag 5 januari 2026" in page.text
    assert (await site.get(f"{base}/proposal")).status_code == 303  # nothing left to confirm


async def test_a_released_proposal_sends_the_participant_back(demo_settings: Settings) -> None:
    async with app_client(demo_settings, FixedClock(NOW)) as early:
        token = await booked(early)
        await _propose(early, token)
    late = FixedClock(NOW + timedelta(hours=48))

    async with app_client(demo_settings, late) as client:
        base = await open_link(client, token)
        response = await client.post(f"{base}/proposal", headers=SAME_ORIGIN)
        page = await client.get(response.headers["location"])

    assert response.headers["location"] == f"{base}?status=proposal_gone"
    assert "niet meer beschikbaar" in page.text
    assert f'href="{base}/reschedule"' in page.text


async def test_the_reschedule_page_suggests_times_but_not_the_held_one(
    site: httpx.AsyncClient,
) -> None:
    base = await _propose(site, await booked(site))

    page = await site.get(f"{base}/reschedule")

    assert "Voorgestelde tijden" in page.text
    assert "Ze worden niet voor u vastgehouden" in page.text
    assert 'start=2026-01-05T08%3A00%3A00Z"' not in page.text


async def test_a_cross_site_confirmation_is_refused(site: httpx.AsyncClient) -> None:
    base = await _propose(site, await booked(site))

    response = await site.post(f"{base}/proposal", headers={"origin": "https://evil.example"})

    assert response.status_code == 403


async def test_a_proposed_slot_that_closed_since_sends_the_participant_back(
    site: httpx.AsyncClient,
) -> None:
    base = await _propose(site, await booked(site))
    app = site._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with app.state.engine.begin() as conn:
        await conn.execute(text("DELETE FROM schedule_rules WHERE weekday = 0"))  # no Mondays

    response = await site.post(f"{base}/proposal", headers=SAME_ORIGIN)

    assert response.headers["location"] == f"{base}?status=proposal_gone"
