"""The flow of a new booking on the standalone page and the widget API (ADR 0121 step 3a).

Every booking records the default flow of its entry path; an optional ``flow`` picks another one
that resolves, and one that does not is refused without any fallback and books nothing. Abuse
case: a flow code of another service or organisation in the booking request.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    BookingRow,
    GroupRow,
    OrganisationRow,
    ServiceFlowRow,
    SlotRow,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.service_flows import FlowState
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.participant import ADA, FRIDAY_0900, SAME_ORIGIN, book
from tests.support.widget import ADA as WIDGET_ADA
from tests.support.widget import BASE, SITE, embed

BAD_CODES = ["unknown", "direct", "Appointment", "a%20b", "x" * 41, "a;b"]


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    await embed(demo_settings)
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


async def _rows(client: httpx.AsyncClient) -> list[BookingRow]:
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        return list((await session.scalars(select(BookingRow).order_by(BookingRow.id))).all())


async def _slots(client: httpx.AsyncClient) -> int:
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        return len((await session.scalars(select(SlotRow.id))).all())


async def _flow_ids(client: httpx.AsyncClient, group: str = "demo") -> dict[str, int]:
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        rows = await session.execute(
            select(ServiceFlowRow.code, ServiceFlowRow.id)
            .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
            .where(GroupRow.slug == group)
        )
        return {code: flow_id for code, flow_id in rows.all()}


async def test_a_page_booking_records_the_default_flow_and_its_revision(
    site: httpx.AsyncClient,
) -> None:
    flows = await _flow_ids(site)

    response = await book(site)

    assert response.status_code == 303
    [row] = await _rows(site)
    assert (row.flow_id, row.flow_revision) == (flows["appointment"], 1)


async def test_an_explicit_flow_is_carried_from_the_calendar_to_the_booking(
    site: httpx.AsyncClient,
) -> None:
    flows = await _flow_ids(site)

    calendar = (await site.get("/g/demo", params={"flow": "appointment"})).text
    form = (
        await site.get("/g/demo/book", params={"start": FRIDAY_0900, "flow": "appointment"})
    ).text
    response = await site.post(
        "/g/demo/book",
        data={"start": FRIDAY_0900, **ADA, "flow": "appointment"},
        headers=SAME_ORIGIN,
    )

    assert 'name="flow" value="appointment"' in calendar  # on the time buttons' form
    assert "flow=appointment" in calendar  # and on the links of the calendar
    assert 'name="flow" value="appointment"' in form
    assert response.status_code == 303
    [row] = await _rows(site)
    assert row.flow_id == flows["appointment"]


async def test_a_page_without_flow_carries_none(site: httpx.AsyncClient) -> None:
    text = (await site.get("/g/demo")).text

    assert 'name="flow"' not in text
    assert "flow=" not in text


@pytest.mark.parametrize("code", BAD_CODES)
async def test_a_page_opened_with_a_flow_that_does_not_resolve_is_refused(
    site: httpx.AsyncClient, code: str
) -> None:
    for path in ("/g/demo", "/g/demo/book"):
        response = await site.get(path, params={"start": FRIDAY_0900, "flow": code})
        assert response.status_code == 422
        assert code.strip() not in response.text.replace("&#34;", "")  # never echoed


@pytest.mark.parametrize("code", BAD_CODES)
async def test_a_booking_post_with_a_flow_that_does_not_resolve_books_nothing(
    site: httpx.AsyncClient, code: str
) -> None:
    response = await site.post(
        "/g/demo/book",
        data={"start": FRIDAY_0900, **ADA, "flow": code},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422  # never a fallback to the default flow
    assert await _rows(site) == []
    assert await _slots(site) == 0


async def test_an_empty_flow_is_not_asked_for(site: httpx.AsyncClient) -> None:
    flows = await _flow_ids(site)

    response = await site.post(
        "/g/demo/book", data={"start": FRIDAY_0900, **ADA, "flow": ""}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    [row] = await _rows(site)
    assert row.flow_id == flows["appointment"]


async def test_an_archived_flow_is_refused_but_the_default_path_keeps_booking(
    site: httpx.AsyncClient,
) -> None:
    app = site._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session, session.begin():
        await session.execute(update(ServiceFlowRow).values(state=FlowState.ARCHIVED.value))

    refused = await site.get("/g/demo", params={"flow": "appointment"})
    implicit = await book(site)

    assert refused.status_code == 422
    assert implicit.status_code == 303  # the service's own settings decide what is offered
    [row] = await _rows(site)
    assert row.flow_id is None


async def test_a_flow_code_of_another_service_is_refused(site: httpx.AsyncClient) -> None:
    """Abuse case: a flow of another service or organisation named in the booking request."""
    app = site._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session, session.begin():
        other_org = OrganisationRow(name="Other", slug="other")
        session.add(other_org)
        await session.flush()
        other = GroupRow(
            organisation_id=other_org.id,
            name="Other",
            slug="other-team",
            timezone="Europe/Amsterdam",
            slot_minutes=30,
            min_notice_minutes=60,
            horizon_days=60,
            phone_mode="optional",
        )
        session.add(other)
        await session.flush()
        other_id = other.id
    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        await tx.ensure_default_flows(other_id)
    async with AsyncSession(app.state.engine) as session, session.begin():
        await session.execute(
            update(ServiceFlowRow)
            .where(ServiceFlowRow.group_id == other_id)
            .values(code="elsewhere")
        )

    page = await site.get("/g/demo", params={"flow": "elsewhere"})
    posted = await site.post(
        "/g/demo/book",
        data={"start": FRIDAY_0900, **ADA, "flow": "elsewhere"},
        headers=SAME_ORIGIN,
    )
    widget = await site.post(
        f"{BASE}/bookings", json={**WIDGET_ADA, "flow": "elsewhere"}, headers={"origin": SITE}
    )

    assert (page.status_code, posted.status_code, widget.status_code) == (422, 422, 422)
    assert widget.json() == {"error": "flow_not_available", "field": None}
    assert await _rows(site) == []


async def test_a_widget_booking_records_the_default_flow(site: httpx.AsyncClient) -> None:
    flows = await _flow_ids(site)

    response = await site.post(f"{BASE}/bookings", json=WIDGET_ADA, headers={"origin": SITE})

    assert response.status_code == 201
    assert "flow" not in response.json()  # nothing observable changes for the host page
    [row] = await _rows(site)
    assert (row.flow_id, row.flow_revision) == (flows["appointment"], 1)


async def test_a_widget_booking_may_name_the_flow(site: httpx.AsyncClient) -> None:
    flows = await _flow_ids(site)

    response = await site.post(
        f"{BASE}/bookings", json={**WIDGET_ADA, "flow": "appointment"}, headers={"origin": SITE}
    )

    assert response.status_code == 201
    [row] = await _rows(site)
    assert row.flow_id == flows["appointment"]


@pytest.mark.parametrize("code", ["unknown", "direct"])
async def test_a_widget_flow_that_does_not_resolve_is_refused_without_a_fallback(
    site: httpx.AsyncClient, code: str
) -> None:
    response = await site.post(
        f"{BASE}/bookings", json={**WIDGET_ADA, "flow": code}, headers={"origin": SITE}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "flow_not_available", "field": None}
    assert await _rows(site) == []
    assert await _slots(site) == 0


@pytest.mark.parametrize("code", ["", "Appointment", "a b", "x" * 41, 7])
async def test_a_malformed_widget_flow_is_refused_before_any_booking_work(
    site: httpx.AsyncClient, code: object
) -> None:
    response = await site.post(
        f"{BASE}/bookings", json={**WIDGET_ADA, "flow": code}, headers={"origin": SITE}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request", "field": None}
    assert await _rows(site) == []


async def test_the_flow_is_in_the_widget_openapi_schema(site: httpx.AsyncClient) -> None:
    spec = (await site.get("/openapi.json")).json()

    request = spec["paths"]["/api/v1/widget/{org}/{group}/bookings"]["post"]["requestBody"]
    properties = request["content"]["application/json"]["schema"]["properties"]
    assert "flow" in properties
    assert properties["flow"]["anyOf"][0]["pattern"] == "^[a-z0-9][a-z0-9-]{0,39}$"


async def test_moving_a_page_booking_keeps_the_flow(site: httpx.AsyncClient) -> None:
    from tests.support.participant import FRIDAY_0930, booked, open_link

    flows = await _flow_ids(site)
    token = await booked(site)
    page = await open_link(site, token)

    moved = await site.post(f"{page}/reschedule", data={"start": FRIDAY_0930}, headers=SAME_ORIGIN)

    assert moved.status_code in (200, 303)
    rows = await _rows(site)
    assert [r.flow_id for r in rows] == [flows["appointment"]] * len(rows)
    assert len(rows) == 2
