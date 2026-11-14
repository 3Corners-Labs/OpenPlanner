"""The flow of a new booking on the standalone page and the widget API (ADR 0121 step 3a).

Every booking records the default flow of its entry path; an optional ``flow`` picks another one
that resolves, and one that does not is refused without any fallback and books nothing. Abuse
case: a flow code of another service or organisation in the booking request.
"""

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    GroupRow,
    OrganisationRow,
    ServiceFlowRow,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.service_flows import FlowState
from tests.fakes.clock import FixedClock
from tests.support.app_client import app_client
from tests.support.domain import NOW
from tests.support.flows import booking_rows, engine_of, flow_ids, offer_direct_calls, slot_count
from tests.support.participant import ADA, FRIDAY_0900, SAME_ORIGIN, book
from tests.support.widget import ADA as WIDGET_ADA
from tests.support.widget import BASE, SITE, embed

BAD_CODES = ["unknown", "direct", "Appointment", "a%20b", "x" * 41, "a;b"]


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    await embed(demo_settings)
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


async def test_a_page_booking_records_the_default_flow_and_its_revision(
    site: httpx.AsyncClient,
) -> None:
    flows = await flow_ids(engine_of(site))

    response = await book(site)

    assert response.status_code == 303
    [row] = await booking_rows(engine_of(site))
    assert (row.flow_id, row.flow_revision) == (flows["appointment"], 1)


async def test_an_explicit_flow_is_carried_from_the_calendar_to_the_booking(
    site: httpx.AsyncClient,
) -> None:
    flows = await flow_ids(engine_of(site))

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
    [row] = await booking_rows(engine_of(site))
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
    assert await booking_rows(engine_of(site)) == []
    assert await slot_count(engine_of(site)) == 0


async def test_an_empty_flow_is_not_asked_for(site: httpx.AsyncClient) -> None:
    flows = await flow_ids(engine_of(site))

    response = await site.post(
        "/g/demo/book", data={"start": FRIDAY_0900, **ADA, "flow": ""}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    [row] = await booking_rows(engine_of(site))
    assert row.flow_id == flows["appointment"]


async def test_an_archived_flow_is_refused_but_the_default_path_keeps_booking(
    site: httpx.AsyncClient,
) -> None:
    async with AsyncSession(engine_of(site)) as session, session.begin():
        await session.execute(update(ServiceFlowRow).values(state=FlowState.ARCHIVED.value))

    refused = await site.get("/g/demo", params={"flow": "appointment"})
    implicit = await book(site)

    assert refused.status_code == 422
    assert implicit.status_code == 303  # the service's own settings decide what is offered
    [row] = await booking_rows(engine_of(site))
    assert row.flow_id is None


async def test_a_flow_code_of_another_service_is_refused(site: httpx.AsyncClient) -> None:
    """Abuse case: a flow of another service or organisation named in the booking request."""
    engine = engine_of(site)
    async with AsyncSession(engine) as session, session.begin():
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
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.ensure_default_flows(other_id)
    async with AsyncSession(engine) as session, session.begin():
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
    assert await booking_rows(engine_of(site)) == []


async def test_a_widget_booking_records_the_default_flow(site: httpx.AsyncClient) -> None:
    flows = await flow_ids(engine_of(site))

    response = await site.post(f"{BASE}/bookings", json=WIDGET_ADA, headers={"origin": SITE})

    assert response.status_code == 201
    assert "flow" not in response.json()  # nothing observable changes for the host page
    [row] = await booking_rows(engine_of(site))
    assert (row.flow_id, row.flow_revision) == (flows["appointment"], 1)


async def test_a_widget_booking_may_name_the_flow(site: httpx.AsyncClient) -> None:
    flows = await flow_ids(engine_of(site))

    response = await site.post(
        f"{BASE}/bookings", json={**WIDGET_ADA, "flow": "appointment"}, headers={"origin": SITE}
    )

    assert response.status_code == 201
    [row] = await booking_rows(engine_of(site))
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
    assert await booking_rows(engine_of(site)) == []
    assert await slot_count(engine_of(site)) == 0


@pytest.mark.parametrize("code", ["", "Appointment", "a b", "x" * 41, 7])
async def test_a_malformed_widget_flow_is_refused_before_any_booking_work(
    site: httpx.AsyncClient, code: object
) -> None:
    response = await site.post(
        f"{BASE}/bookings", json={**WIDGET_ADA, "flow": code}, headers={"origin": SITE}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request", "field": None}
    assert await booking_rows(engine_of(site)) == []


async def test_the_flow_is_in_the_widget_openapi_schema(site: httpx.AsyncClient) -> None:
    spec = (await site.get("/openapi.json")).json()

    request = spec["paths"]["/api/v1/widget/{org}/{group}/bookings"]["post"]["requestBody"]
    properties = request["content"]["application/json"]["schema"]["properties"]
    assert "flow" in properties
    assert properties["flow"]["anyOf"][0]["pattern"] == "^[a-z0-9][a-z0-9-]{0,39}$"


def _schemas(node: object) -> Iterator[dict[str, object]]:
    """Yield every object in an OpenAPI fragment that may carry ``properties`` or ``required``."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _schemas(value)
    elif isinstance(node, list):
        for value in node:
            yield from _schemas(value)


async def test_no_api_v1_schema_requires_a_flow(site: httpx.AsyncClient) -> None:
    # ADR 0121 step 8: the flow is additive in /api/v1, so a client from before flows still works.
    spec = (await site.get("/openapi.json")).json()
    # What a client sends: parameters and request bodies (a new response field breaks no client).
    used = [
        {key: operation[key] for key in ("parameters", "requestBody") if key in operation}
        for path, item in spec["paths"].items()
        if path.startswith("/api/v1/")
        for operation in item.values()
        if isinstance(operation, dict)
    ]
    components = spec.get("components", {}).get("schemas", {})
    fragments: list[object] = list(used)
    names: set[str] = set()
    while True:  # follow references until no new component is reached
        found = {
            ref.rsplit("/", 1)[-1]
            for node in _schemas(fragments)
            if isinstance(ref := node.get("$ref"), str)
        }
        if found <= names:
            break
        fragments += [components[name] for name in found - names]
        names |= found

    required = [
        node
        for node in _schemas(fragments)
        if isinstance(listed := node.get("required"), list) and "flow" in listed
    ]
    parameters = [
        node
        for node in _schemas(used)
        if node.get("in") == "query" and node.get("name") == "flow" and node.get("required")
    ]
    assert used
    assert required == []
    assert parameters == []


async def test_moving_a_page_booking_keeps_the_flow(site: httpx.AsyncClient) -> None:
    from tests.support.participant import FRIDAY_0930, booked, open_link

    flows = await flow_ids(engine_of(site))
    token = await booked(site)
    page = await open_link(site, token)

    moved = await site.post(f"{page}/reschedule", data={"start": FRIDAY_0930}, headers=SAME_ORIGIN)

    assert moved.status_code in (200, 303)
    rows = await booking_rows(engine_of(site))
    assert [r.flow_id for r in rows] == [flows["appointment"]] * len(rows)
    assert len(rows) == 2


async def test_an_appointment_only_flow_is_refused_on_the_direct_page(
    site: httpx.AsyncClient,
) -> None:
    assert "appointment" in await flow_ids(engine_of(site))

    page = await site.get("/g/demo/direct", params={"flow": "appointment"})

    assert page.status_code == 422  # it exists, but it does not offer the on-demand path


async def test_an_on_demand_only_flow_is_refused_on_the_booking_pages(
    site: httpx.AsyncClient,
) -> None:
    await offer_direct_calls(engine_of(site))
    assert "direct" in await flow_ids(engine_of(site))

    calendar = await site.get("/g/demo", params={"flow": "direct"})
    posted = await site.post(
        "/g/demo/book", data={"start": FRIDAY_0900, **ADA, "flow": "direct"}, headers=SAME_ORIGIN
    )

    assert (calendar.status_code, posted.status_code) == (422, 422)
    assert await booking_rows(engine_of(site)) == []


async def test_page_views_without_a_flow_read_no_flows(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*args: object) -> None:
        raise AssertionError("a page without a flow must not read flows")

    monkeypatch.setattr("app.domain.services.flow_for", forbidden)

    for path in ("/g/demo", f"/g/demo/book?start={FRIDAY_0900}", "/g/demo/direct"):
        assert (await site.get(path)).status_code == 200, path


async def test_the_widget_group_load_refuses_a_flow_that_does_not_resolve(
    site: httpx.AsyncClient,
) -> None:
    await offer_direct_calls(engine_of(site))
    resolved = await site.get(f"{BASE}/", params={"flow": "appointment"}, headers={"origin": SITE})
    other_path = await site.get(f"{BASE}/", params={"flow": "direct"}, headers={"origin": SITE})
    unknown = await site.get(f"{BASE}/", params={"flow": "Not A Code"}, headers={"origin": SITE})
    plain = await site.get(f"{BASE}/", headers={"origin": SITE})

    assert (resolved.status_code, plain.status_code) == (200, 200)
    for refused in (other_path, unknown):
        assert refused.status_code == 422
        assert refused.json() == {"error": "flow_not_available", "field": None}
