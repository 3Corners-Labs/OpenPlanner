"""The simulator on an admin booking page: events take the real webhook path (not in prod)."""

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import NamedTuple

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.services import BookingService
from app.domain.simulation import SIMULATED_LINK
from app.main import create_app
from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, Site
from tests.admin.test_admin_flow import audit_events
from tests.admin.test_call_routes import FRIDAY_0900
from tests.fakes.clock import FixedClock
from tests.fakes.identity import FakeIdentityProvider
from tests.support.domain import ADA, NOW, TEST_TOKEN_KEY, book_confirmed

HOST = "host@example.nl"


class Booked(NamedTuple):
    """The site, a confirmed booking in the demo group and its management token."""

    site: Site
    booking_id: int
    token: str


def _secrets(settings: Settings, admin_db: str) -> Settings:
    return settings.model_copy(
        update={
            "database_url": admin_db,
            "simulator_enabled": True,
            "webex_webhook_secret": SecretStr("webex-test-secret"),
            "wxcc_webhook_secret": SecretStr("wxcc-test-secret"),
        }
    )


async def _site(settings: Settings) -> AsyncIterator[Booked]:
    engine = create_engine(settings.database_url)
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:  # the demo group offers video and the trial call
        group = await tx.get_group("demo")
        assert group is not None
        await tx.save_group(replace(group, video_host_email=HOST))
    clock = FixedClock(NOW)
    receipt = await book_confirmed(
        BookingService(uow, clock, TEST_TOKEN_KEY),
        group_slug="demo",
        start=FRIDAY_0900,
        contact=ADA,
        locale="nl",
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
        yield Booked(Site(client, idp, clock), receipt.booking.id or 0, receipt.management_token)


@pytest.fixture
async def booked(settings: Settings, admin_db: str) -> AsyncIterator[Booked]:
    async for value in _site(_secrets(settings, admin_db)):
        yield value


async def _play(site: Site, booking_id: int, event: str) -> httpx.Response:
    return await site.client.post(
        f"/admin/groups/demo/bookings/{booking_id}/simulate",
        data={"event": event},
        headers=SAME_ORIGIN,
    )


async def test_a_whole_call_can_be_played_and_lands_on_the_timeline(
    booked: Booked,
) -> None:
    site, booking_id, _ = booked
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


async def test_a_second_click_counts_again(booked: Booked) -> None:
    site, booking_id, _ = booked
    await site.login_as(ORG_ADMIN)
    await _play(site, booking_id, "prepare")

    await _play(site, booking_id, "guest_joined")
    await _play(site, booking_id, "guest_joined")

    page = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text
    assert "<div><dt>Deelnemers</dt><dd>2</dd></div>" in page


@pytest.mark.parametrize("event", ["host_joined", "trial_entered"])
async def test_without_ids_the_simulator_says_so(booked: Booked, event: str) -> None:
    site, booking_id, _ = booked
    await site.login_as(ORG_ADMIN)

    response = await _play(site, booking_id, event)

    assert "done=simulator_no_ids" in response.headers["location"]


async def test_unknown_events_and_cross_site_posts_are_refused(booked: Booked) -> None:
    site, booking_id, _ = booked
    await site.login_as(ORG_ADMIN)

    unknown = await _play(site, booking_id, "drop_tables")
    cross = await site.client.post(
        f"/admin/groups/demo/bookings/{booking_id}/simulate",
        data={"event": "prepare"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert unknown.status_code == 400
    assert cross.status_code == 403


async def test_group_admins_see_no_simulator(booked: Booked) -> None:
    site, booking_id, _ = booked
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text

    assert "Simulator (alleen in test)" not in page
    assert "Simulator staat aan" in page  # the bar says it is switched on


async def test_without_the_switch_there_is_no_simulator(settings: Settings, admin_db: str) -> None:
    # SIMULATOR_ENABLED off (the default, and the only value production accepts).
    switched_off = _secrets(settings, admin_db).model_copy(update={"simulator_enabled": False})
    async for site, booking_id, _ in _site(switched_off):
        await site.login_as(ORG_ADMIN)
        page = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text
        response = await _play(site, booking_id, "prepare")
        assert "Simulator (alleen in test)" not in page
        assert "Simulator staat aan" not in page
        assert response.status_code == 403


async def test_the_trial_call_can_be_played_and_leaves_the_call_status_alone(
    booked: Booked,
) -> None:
    site, booking_id, token = booked
    await site.login_as(ORG_ADMIN)
    page = f"/admin/groups/demo/bookings/{booking_id}"
    await _play(site, booking_id, "prepare")

    # The participant's trial page now offers the way in, to the stand-in meeting (ADR 0075).
    trial_page = (await site.client.get(f"/b/{token}/test")).text
    assert "Naar het proefgesprek" in trial_page
    way_in = await site.client.get(f"/b/{token}/test/go")
    assert way_in.status_code == 303
    assert way_in.headers["location"] == f"{SIMULATED_LINK}?launchApp=true"

    for event in ("trial_entered", "trial_left"):
        response = await _play(site, booking_id, event)
        assert response.headers["location"] == f"{page}?done=simulated#simulator", event

    html = (await site.client.get(page)).text
    assert "Proefgesprek geopend door de deelnemer" in html
    assert "Deelnemer ging het proefgesprek in" in html
    assert "Deelnemer kwam het proefgesprek binnen" in html
    assert "Deelnemer verliet het proefgesprek" in html
    assert "<div><dt>Deelnemers</dt><dd>0</dd></div>" in html  # the trial call never counts
    assert "<dd>Nee</dd>" in html  # nor does it put the agent in the call
