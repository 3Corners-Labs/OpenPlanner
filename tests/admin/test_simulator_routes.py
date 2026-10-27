"""The simulator on an admin booking page: events take the real webhook path (not in prod)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.services import BookingService
from app.main import create_app
from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, Site
from tests.admin.test_admin_flow import audit_events
from tests.admin.test_call_routes import FRIDAY_0900
from tests.fakes.clock import FixedClock
from tests.fakes.identity import FakeIdentityProvider
from tests.support.domain import ADA, NOW, TEST_TOKEN_KEY


def _secrets(settings: Settings, admin_db: str) -> Settings:
    return settings.model_copy(
        update={
            "database_url": admin_db,
            "webex_webhook_secret": SecretStr("webex-test-secret"),
            "wxcc_webhook_secret": SecretStr("wxcc-test-secret"),
        }
    )


async def _site(settings: Settings) -> AsyncIterator[tuple[Site, int]]:
    engine = create_engine(settings.database_url)
    await seed_demo(engine)
    clock = FixedClock(NOW)
    receipt = await BookingService(SqlUnitOfWork(engine), clock, TEST_TOKEN_KEY).book(
        group_slug="demo", start=FRIDAY_0900, contact=ADA, locale="nl"
    )
    await engine.dispose()
    idp = FakeIdentityProvider()
    app = create_app(settings, clock, idp)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield Site(client, idp, clock), receipt.booking.id or 0


@pytest.fixture
async def booked(settings: Settings, admin_db: str) -> AsyncIterator[tuple[Site, int]]:
    async for value in _site(_secrets(settings, admin_db)):
        yield value


async def _play(site: Site, booking_id: int, event: str) -> httpx.Response:
    return await site.client.post(
        f"/admin/groups/demo/bookings/{booking_id}/simulate",
        data={"event": event},
        headers=SAME_ORIGIN,
    )


async def test_a_whole_call_can_be_played_and_lands_on_the_timeline(
    booked: tuple[Site, int],
) -> None:
    site, booking_id = booked
    await site.login_as(ORG_ADMIN)
    page = f"/admin/groups/demo/bookings/{booking_id}"
    before = (await site.client.get(page)).text
    assert "Simulator (alleen in test)" in before
    assert "Nep-meeting en -taak aanmaken" in before

    prepared = await _play(site, booking_id, "prepare")
    assert prepared.headers["location"] == f"{page}?done=simulated_prepared#simulator"
    for event in ("task_connected", "host_joined", "guest_joined", "meeting_ended"):
        response = await _play(site, booking_id, event)
        assert response.headers["location"] == f"{page}?done=simulated#simulator", event

    after = (await site.client.get(page)).text
    assert "Het event is afgeleverd" not in after  # no ?done= on this plain GET
    assert "Nep-meeting en -taak aanmaken" not in after  # prepared
    assert "<dd>Ja</dd>" in after  # agent (host) joined
    actions = [b for _, b, _, _ in await audit_events(site)]
    assert actions.count("simulator.event") == 4
    assert "simulator.prepare" in actions


async def test_a_second_click_counts_again(booked: tuple[Site, int]) -> None:
    site, booking_id = booked
    await site.login_as(ORG_ADMIN)
    await _play(site, booking_id, "prepare")

    await _play(site, booking_id, "guest_joined")
    await _play(site, booking_id, "guest_joined")

    page = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text
    assert "<div><dt>Deelnemers</dt><dd>2</dd></div>" in page


async def test_without_ids_the_simulator_says_so(booked: tuple[Site, int]) -> None:
    site, booking_id = booked
    await site.login_as(ORG_ADMIN)

    response = await _play(site, booking_id, "host_joined")

    assert "done=simulator_no_ids" in response.headers["location"]


async def test_unknown_events_and_cross_site_posts_are_refused(booked: tuple[Site, int]) -> None:
    site, booking_id = booked
    await site.login_as(ORG_ADMIN)

    unknown = await _play(site, booking_id, "drop_tables")
    cross = await site.client.post(
        f"/admin/groups/demo/bookings/{booking_id}/simulate",
        data={"event": "prepare"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert unknown.status_code == 400
    assert cross.status_code == 403


async def test_group_admins_see_no_simulator(booked: tuple[Site, int]) -> None:
    site, booking_id = booked
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text

    assert "Simulator" not in page


async def test_in_production_there_is_no_simulator(settings: Settings, admin_db: str) -> None:
    # Production refuses to start with development defaults, so switch only the service off
    # the way the composition root does when ENVIRONMENT=prod.
    async for site, booking_id in _site(_secrets(settings, admin_db)):
        site.client._transport.app.state.simulation_service.enabled = False  # type: ignore[attr-defined]  # noqa: SLF001
        await site.login_as(ORG_ADMIN)
        page = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text
        response = await _play(site, booking_id, "prepare")
        assert "Simulator" not in page
        assert response.status_code == 403
