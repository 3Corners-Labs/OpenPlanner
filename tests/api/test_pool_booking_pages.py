"""Booking on a counted pool through the standalone page and the widget API (ADR 0122 step 5).

The demo service gets a flow ``advies`` on a counted pool staffed Fridays 13:00–15:00
Amsterdam with 2 people (20-minute appointments), beside its own staffing (09:00 slots). With
the rollback gate shut, the flow is refused everywhere and nothing is stored; opened by a
monkeypatch, the pages and the widget offer and book the pool's times.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import SlotRow
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.flows import booking_rows, engine_of, slot_count
from tests.support.participant import ADA, FRIDAY_0900, SAME_ORIGIN, book, booked, open_link
from tests.support.pool_flows import PoolFlow, block, open_gate, seed_pool_flow, shut_gate
from tests.support.widget import ADA as WIDGET_ADA
from tests.support.widget import BASE, SITE, embed

AT_1300 = "2026-01-02T12:00:00Z"
AT_1320 = "2026-01-02T12:20:00Z"
FRIDAY = "2026-01-02"


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    await embed(demo_settings)
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


async def _pool_flow(site: httpx.AsyncClient) -> PoolFlow:
    uow = SqlUnitOfWork(engine_of(site))
    async with uow.read() as tx:
        group = await tx.get_group("demo")
    assert group is not None
    return await seed_pool_flow(
        uow, group.organisation_id, group.id, [block(weekday=4, opens="13:00", closes="15:00")]
    )


async def _pool_slots(site: httpx.AsyncClient) -> list[SlotRow]:
    async with AsyncSession(engine_of(site)) as session:
        rows = await session.scalars(select(SlotRow).where(SlotRow.capacity_pool_id.is_not(None)))
        return list(rows.all())


# --- the gate shut: refused everywhere, nothing stored ---------------------------------------


async def test_with_the_gate_shut_the_pool_flow_is_refused_on_every_entry(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    shut_gate(monkeypatch)  # explicitly: the default opens with G10
    await _pool_flow(site)

    calendar = await site.get("/g/demo", params={"flow": "advies"})
    form = await site.get("/g/demo/book", params={"start": AT_1300, "flow": "advies"})
    posted = await book(site, AT_1300, flow="advies")
    widget_load = await site.get(f"{BASE}/", params={"flow": "advies"}, headers={"origin": SITE})
    widget_times = await site.get(
        f"{BASE}/availability", params={"flow": "advies"}, headers={"origin": SITE}
    )
    widget_post = await site.post(
        f"{BASE}/bookings",
        json={**WIDGET_ADA, "start": AT_1300, "flow": "advies"},
        headers={"origin": SITE},
    )

    statuses = [r.status_code for r in (calendar, form, posted, widget_load, widget_times)]
    assert statuses == [422] * 5
    assert widget_post.status_code == 422
    assert widget_post.json() == {"error": "flow_not_available", "field": None}
    assert await booking_rows(engine_of(site)) == []
    assert await slot_count(engine_of(site)) == 0


async def test_with_the_gate_shut_the_pages_without_a_flow_do_not_change(
    site: httpx.AsyncClient,
) -> None:
    before = (await site.get("/g/demo", params={"day": FRIDAY})).text
    widget_before = (
        await site.get(f"{BASE}/availability", params={"day": FRIDAY}, headers={"origin": SITE})
    ).json()
    await _pool_flow(site)

    after = (await site.get("/g/demo", params={"day": FRIDAY})).text
    widget_after = (
        await site.get(f"{BASE}/availability", params={"day": FRIDAY}, headers={"origin": SITE})
    ).json()
    response = await book(site)

    assert after == before
    assert widget_after == widget_before
    assert response.status_code == 303
    assert await _pool_slots(site) == []


# --- the gate open: the pool's times, booked on the pool -------------------------------------


async def test_the_standalone_page_offers_and_books_the_pools_times(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_gate(monkeypatch)
    made = await _pool_flow(site)

    calendar = (await site.get("/g/demo", params={"flow": "advies", "day": FRIDAY})).text
    form = await site.get("/g/demo/book", params={"start": AT_1300, "flow": "advies"})
    response = await site.post(
        "/g/demo/book", data={"start": AT_1300, **ADA, "flow": "advies"}, headers=SAME_ORIGIN
    )

    assert AT_1300 in calendar
    assert FRIDAY_0900 not in calendar  # the service's own time is not the flow's
    assert form.status_code == 200
    assert response.status_code == 303
    [row] = await booking_rows(engine_of(site))
    assert row.flow_id == made.flow_id
    [slot] = await _pool_slots(site)
    assert slot.capacity_pool_id == made.pool_id
    assert slot.id == row.slot_id


async def test_a_time_off_the_pools_grid_sends_the_form_back(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_gate(monkeypatch)
    await _pool_flow(site)

    form = await site.get("/g/demo/book", params={"start": FRIDAY_0900, "flow": "advies"})
    posted = await book(site, FRIDAY_0900, flow="advies")

    assert form.status_code == 303
    assert posted.status_code == 409
    assert await booking_rows(engine_of(site)) == []


async def test_the_widget_offers_and_books_the_pools_times(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_gate(monkeypatch)
    made = await _pool_flow(site)

    times = await site.get(
        f"{BASE}/availability", params={"flow": "advies", "day": FRIDAY}, headers={"origin": SITE}
    )
    response = await site.post(
        f"{BASE}/bookings",
        json={**WIDGET_ADA, "start": AT_1300, "flow": "advies"},
        headers={"origin": SITE},
    )

    assert times.status_code == 200
    assert AT_1300 in times.text
    assert response.status_code == 201
    [row] = await booking_rows(engine_of(site))
    assert row.flow_id == made.flow_id
    assert len(await _pool_slots(site)) == 1


async def test_the_participant_moves_a_pool_booking_on_the_pool(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_gate(monkeypatch)
    await _pool_flow(site)
    page = await open_link(site, await booked(site, AT_1300, flow="advies"))

    picker = (await site.get(f"{page}/reschedule", params={"day": FRIDAY})).text
    moved = await site.post(f"{page}/reschedule", data={"start": AT_1320}, headers=SAME_ORIGIN)

    assert AT_1320 in picker
    assert FRIDAY_0900 not in picker
    assert moved.status_code in (200, 303)
    rows = await booking_rows(engine_of(site))
    assert len(rows) == 2
    assert len({row.flow_id for row in rows}) == 1
    assert {slot.start.replace(tzinfo=None).isoformat() for slot in await _pool_slots(site)} == {
        "2026-01-02T12:00:00",
        "2026-01-02T12:20:00",
    }
