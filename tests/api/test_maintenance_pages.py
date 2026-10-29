"""Citizen pages and the machine API during maintenance (ADR 0038 §2, MM-3).

Abuse cases from docs/threat-model.md: a citizen POST slipping past the rules (every public
route is either closed or explicitly open), and a machine-API write.
"""

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.api.maintenance import (
    BOOKING_ROUTES,
    GROUP_ROUTES,
    OPEN_ROUTES,
    citizen_gate,
    machine_gate,
    register_machine_maintenance,
)
from app.config import Settings
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.maintenance import MaintenanceReason
from app.domain.maintenance_service import MaintenanceService
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW

SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
TOKEN = "A" * 43
PHONE = ContactOption.create(ContactKind.PHONE, "14 020")


class Site:
    def __init__(self, client: httpx.AsyncClient, app: FastAPI) -> None:
        self.client = client
        self.app = app

    @property
    def maintenance(self) -> MaintenanceService:
        found: MaintenanceService = self.app.state.maintenance_service
        return found

    async def switch_on(self, expected_end: bool = False) -> None:
        if expected_end:
            await self.maintenance._switch_on(  # noqa: SLF001 - the portal's path, no principal
                "s-1", MaintenanceReason.UPDATE, NOW + timedelta(hours=2)
            )
        else:
            await self.maintenance.switch_on_host("host-cli", MaintenanceReason.UPDATE)


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[Site]:
    app = create_app(demo_settings, FixedClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield Site(client, app)


CLOSED = [
    ("GET", "/g/demo"),
    ("GET", "/g/demo/book?start=2026-01-05T09:00"),
    ("POST", "/g/demo/book"),
    ("GET", "/g/demo/direct"),
    ("POST", "/g/demo/direct"),
    ("GET", f"/b/{TOKEN}/cancel"),
    ("POST", f"/b/{TOKEN}/cancel"),
    ("GET", f"/b/{TOKEN}/reschedule"),
    ("POST", f"/b/{TOKEN}/reschedule"),
]


@pytest.mark.parametrize(("method", "path"), CLOSED)
async def test_citizen_forms_and_writes_answer_503_with_retry_after(
    site: Site, method: str, path: str
) -> None:
    await site.switch_on()

    response = await site.client.request(method, path, headers=SAME_ORIGIN)

    assert response.status_code == 503
    assert response.headers["retry-after"] == "300"
    assert "We voeren onderhoud uit" in response.text


async def test_without_maintenance_the_pages_work(site: Site) -> None:
    response = await site.client.get("/g/demo")

    assert response.status_code == 200
    assert "We voeren onderhoud uit" not in response.text


async def test_group_page_shows_branding_expected_end_and_contact_block(site: Site) -> None:
    engine = create_engine(site.app.state.settings.database_url)
    try:
        uow = SqlUnitOfWork(engine)
        async with uow.locked() as tx:
            demo = await tx.get_organisation("demo")
            assert demo is not None
            await tx.save_contact_block(demo.id, ContactBlock((PHONE,)))
    finally:
        await engine.dispose()
    await site.switch_on(expected_end=True)

    page = (await site.client.get("/g/demo?lang=en")).text

    assert "We are carrying out maintenance" in page
    assert "We expect to be done on Thursday, January 1, 2026, 11:00." in page
    assert 'href="tel:14020"' in page
    assert "/o/demo/theme.css" in page


async def test_without_a_contact_block_it_says_to_try_later(site: Site) -> None:
    await site.switch_on()

    page = (await site.client.get("/g/demo")).text

    assert "Probeer het later opnieuw." in page
    assert "We verwachten" not in page


async def test_a_management_link_page_never_tells_whether_the_token_exists(site: Site) -> None:
    await site.switch_on()

    unknown = await site.client.get(f"/b/{TOKEN}/cancel")
    other = await site.client.get(f"/b/{'B' * 43}/reschedule")

    assert unknown.status_code == other.status_code == 503
    assert "/o/demo/theme.css" not in unknown.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", f"/b/{TOKEN}"),
        ("GET", f"/b/{TOKEN}/join"),
        ("GET", f"/b/{TOKEN}/wait"),
        ("POST", "/webhooks/webex"),
        ("POST", "/webhooks/wxcc"),
        ("GET", "/o/demo/theme.css"),
    ],
)
async def test_reads_health_and_webhooks_stay_open(site: Site, method: str, path: str) -> None:
    await site.switch_on()

    response = await site.client.request(method, path)

    assert response.status_code != 503, path
    if path in ("/healthz", "/readyz"):
        assert response.status_code == 200


async def test_purge_jobs_are_not_touched() -> None:
    # The worker never asks the gate: the privacy jobs keep their schedule (ADR 0038). It only
    # watches how long maintenance lasts (MaintenanceWatch), which refuses nothing.
    from pathlib import Path

    import app.jobs as jobs_package

    sources = [p.read_text() for p in Path(jobs_package.__file__).parent.glob("*.py")]
    assert not any("MaintenanceService" in source for source in sources)
    assert not any(".refuse(" in source or ".status()" in source for source in sources)


def test_every_public_route_is_closed_or_explicitly_open(demo_settings: Settings) -> None:
    app = create_app(demo_settings, FixedClock(NOW))
    public = {
        route.path: gated
        for route, gated in _routes_with_gate(app.routes)
        if not route.path.startswith("/admin") and route.path != "/openapi.json"
    }

    assert set(public) == GROUP_ROUTES | BOOKING_ROUTES | OPEN_ROUTES
    assert [path for path, gated in public.items() if not gated] == []


def _routes_with_gate(routes: list[object], gated: bool = False) -> list[tuple[APIRoute, bool]]:
    """Every route with whether ``citizen_gate`` guards it (on the route or its include)."""
    found: list[tuple[APIRoute, bool]] = []
    for route in routes:
        if isinstance(route, APIRoute):
            own = any(d.dependency is citizen_gate for d in route.dependencies)
            found.append((route, gated or own))
        context = getattr(route, "include_context", None)
        inner = getattr(route, "original_router", None)
        if inner is not None:
            included = context is not None and any(
                d.dependency is citizen_gate for d in context.dependencies
            )
            found.extend(_routes_with_gate(inner.routes, gated or included))
    return found


# --- machine API ---------------------------------------------------------------------------


@pytest.fixture
async def machine(settings: Settings, database_url: str) -> AsyncIterator[Site]:
    """A machine API with one write route besides the real ones (none write today)."""
    on = settings.model_copy(update={"database_url": database_url, "agent_api_enabled": True})
    app = create_app(on, FixedClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield Site(client, app)


def _write_app(site: Site) -> FastAPI:
    probe = FastAPI(dependencies=[Depends(machine_gate)])
    register_machine_maintenance(probe)
    probe.state.maintenance_service = site.maintenance
    probe.state.settings = site.app.state.settings

    @probe.post("/groups/{slug}/bookings")
    async def write(request: Request, slug: str) -> dict[str, str]:
        return {"ok": slug}

    @probe.get("/groups")
    async def read() -> list[str]:
        return []

    return probe


async def test_machine_writes_answer_503_maintenance_reads_stay_open(machine: Site) -> None:
    probe = _write_app(machine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=probe), base_url="http://testserver"
    ) as client:
        assert (await client.post("/groups/demo/bookings")).status_code == 200
        await machine.switch_on()

        refused = await client.post("/groups/demo/bookings")
        assert refused.status_code == 503
        assert refused.json() == {"error": "maintenance"}
        assert refused.headers["retry-after"] == "300"
        assert (await client.get("/groups")).status_code == 200


async def test_machine_token_endpoint_stays_open(machine: Site) -> None:
    await machine.switch_on()

    response = await machine.client.post("/api/agent/v1/oauth/token", data={})

    assert response.status_code != 503
