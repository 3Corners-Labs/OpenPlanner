"""The call page is a service's choice (ADR 0123 step 6a): its routes and the participant's way in.

Three routes answer only for a booking of a service that chose ``embedded`` while the platform
switch is on; the join page and ``/join/go`` lead there, and the video platform's own address stays
closed around the page unless the service's option allows it. Each case of the threat model's
flow section for this step has a test here: a service that did not choose, an outsider who must
learn nothing about the service, another booking's session, the platform's address around the page,
and the kill switch.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.adapters.db.models import BookingRow
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.booking import MeetingRef
from app.domain.call_page import OtherWayOffer
from app.domain.calls import EventKind
from app.domain.scheduling import JoinClient
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo, webex_cc
from tests.support.call_page import set_group
from tests.support.domain import NOW
from tests.support.participant import (
    FRIDAY_0900,
    FRIDAY_0930,
    SAME_ORIGIN,
    book,
    confirm_newest,
    open_link,
)

START = datetime.fromisoformat(FRIDAY_0900.replace("Z", "+00:00"))
LINK = "https://site.webex.com/site/j.php?MTID=m-guest"
BROWSER_LINK = LINK + "&launchApp=true"
HOST = "host@example.nl"
ROUTES = [
    ("get", "/call"),
    ("post", "/call/token"),
    ("get", "/call/staff"),
]
"""Method and path of every route of the call page."""


@dataclass
class Site:
    """The app with an open booking of Ada in the demo group and a meeting for it."""

    client: httpx.AsyncClient
    base: str
    token: str
    booking_id: int
    neighbour_token: str = ""

    async def ask(self, method: str, path: str, client: httpx.AsyncClient | None = None) -> Any:
        """Ask a route of the call page as the participant's browser does."""
        who = client or self.client
        headers = SAME_ORIGIN if method == "post" else {}
        return await getattr(who, method)(f"{self.base}{path}", headers=headers)

    def stranger(self) -> httpx.AsyncClient:
        """A browser without the participant's cookie."""
        return httpx.AsyncClient(
            transport=self.client._transport,  # type: ignore[attr-defined]  # noqa: SLF001
            base_url="http://testserver",
        )


@asynccontextmanager
async def running(
    demo_settings: Settings,
    *,
    client: JoinClient,
    enabled: bool = True,
    option: OtherWayOffer | None = None,
    organisation_option: str | None = None,
    neighbour: bool = False,
) -> AsyncIterator[Site]:
    """Run the app with the demo group on ``client``, an open booking and the switch as given."""
    changes: dict[str, Any] = {"embedded_call_enabled": enabled}
    if organisation_option is not None:
        changes["embedded_call_webex_option"] = organisation_option
    settings = demo_settings.model_copy(update=changes)
    clock = FixedClock(NOW)
    video = FakeVideo()
    async with app_client(
        settings, clock, Integrations(None, video, webex_cc(FakeDispatcher()))
    ) as http:
        await set_group(
            http,
            "demo",
            video_host_email=HOST,
            join_client=client,
            embedded_call_webex_option=option,
        )
        assert (await book(http)).status_code == 303
        token = await confirm_newest(http)
        app: Any = http._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        async with app.state.engine.connect() as conn:
            booking_id = int((await conn.execute(select(BookingRow.id))).scalars().all()[-1])
        async with SqlUnitOfWork(app.state.engine).locked() as tx:
            await tx.save_meeting(booking_id, MeetingRef("meeting-1", LINK))
        other = ""
        if neighbour:  # a second participant's booking, made while there is still notice
            assert (await book(http, FRIDAY_0930)).status_code == 303
            other = await confirm_newest(http)
        clock.advance(START - timedelta(minutes=5) - NOW)
        yield Site(http, await open_link(http, token), token, booking_id, other)


# --- the three routes answer per service ---------------------------------------------------


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_a_service_that_chose_the_call_page_gets_it(
    demo_settings: Settings, method: str, path: str
) -> None:
    async with running(demo_settings, client=JoinClient.EMBEDDED) as site:
        response = await site.ask(method, path)

    assert response.status_code == 200


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("other", [JoinClient.BROWSER, JoinClient.CHOICE])
async def test_a_service_that_did_not_choose_it_answers_as_an_address_that_does_not_exist(
    demo_settings: Settings, method: str, path: str, other: JoinClient
) -> None:
    async with running(demo_settings, client=other) as site:
        response = await site.ask(method, path)

    assert response.status_code == 404


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_the_switch_is_the_kill_switch_whatever_the_service_says(
    demo_settings: Settings, method: str, path: str
) -> None:
    async with running(demo_settings, client=JoinClient.EMBEDDED, enabled=False) as site:
        response = await site.ask(method, path)

    assert response.status_code == 404


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_an_outsider_learns_nothing_about_the_service(
    demo_settings: Settings, method: str, path: str
) -> None:
    """Without the participant's session the answer is the same for every service."""
    answers = []
    async with running(demo_settings, client=JoinClient.EMBEDDED) as site:
        async with site.stranger() as outsider:
            answers.append(await site.ask(method, path, outsider))
            await set_group(site.client, "demo", join_client=JoinClient.BROWSER)
            answers.append(await site.ask(method, path, outsider))

    first, second = answers
    assert (first.status_code, first.text) == (second.status_code, second.text)
    assert first.status_code == 403
    assert "set-cookie" not in first.headers


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_another_bookings_session_reaches_nothing_of_an_embedded_service(
    demo_settings: Settings, method: str, path: str
) -> None:
    async with running(demo_settings, client=JoinClient.EMBEDDED, neighbour=True) as site:
        async with site.stranger() as neighbour:
            await open_link(neighbour, site.neighbour_token)
            response = await site.ask(method, path, neighbour)

    assert response.status_code == 403


# --- the participant's way in -----------------------------------------------------------------


async def test_the_join_page_leads_to_the_call_page_for_an_embedded_service(
    demo_settings: Settings,
) -> None:
    async with running(demo_settings, client=JoinClient.EMBEDDED) as site:
        page = (await site.client.get(f"{site.base}/join?lang=en")).text

    assert f'href="{site.base}/call"' in page
    assert "/join/go" not in page
    assert "The call opens in this browser" in page
    assert "Webex" not in page  # participant-facing text never names the supplier


@pytest.mark.parametrize("client", [JoinClient.BROWSER, JoinClient.CHOICE])
async def test_the_join_page_of_another_service_still_leads_to_the_redirect(
    demo_settings: Settings, client: JoinClient
) -> None:
    async with running(demo_settings, client=client) as site:
        page = (await site.client.get(f"{site.base}/join")).text

    assert f'href="{site.base}/join/go"' in page
    assert f'href="{site.base}/call"' not in page


async def test_a_held_embedded_choice_acts_as_the_default_while_the_switch_is_off(
    demo_settings: Settings,
) -> None:
    async with running(demo_settings, client=JoinClient.EMBEDDED, enabled=False) as site:
        page = (await site.client.get(f"{site.base}/join")).text
        go = await site.client.get(f"{site.base}/join/go")

    assert f'href="{site.base}/join/go"' in page
    assert go.headers["location"] == BROWSER_LINK  # the redirect they got before, never nowhere


@pytest.mark.parametrize(
    ("option", "closed"),
    [
        (OtherWayOffer.HIDDEN, True),
        (OtherWayOffer.ON_FAILURE, False),
        (OtherWayOffer.ALWAYS, False),
    ],
)
async def test_the_platforms_address_is_closed_around_the_page_unless_the_option_allows_it(
    demo_settings: Settings, option: OtherWayOffer, closed: bool
) -> None:
    async with running(demo_settings, client=JoinClient.EMBEDDED, option=option) as site:
        go = await site.client.get(f"{site.base}/join/go")

    assert go.status_code == 303
    assert go.headers["location"] == (f"{site.base}/call" if closed else BROWSER_LINK)


@pytest.mark.parametrize(
    ("option", "organisation", "closed"),
    [
        (None, "hidden", True),
        (None, "always", False),
        (OtherWayOffer.HIDDEN, "always", True),
        (OtherWayOffer.ALWAYS, "hidden", False),
    ],
)
async def test_a_service_without_an_option_follows_its_organisations_value(
    demo_settings: Settings, option: OtherWayOffer | None, organisation: str, closed: bool
) -> None:
    async with running(
        demo_settings,
        client=JoinClient.EMBEDDED,
        option=option,
        organisation_option=organisation,
    ) as site:
        go = await site.client.get(f"{site.base}/join/go")
        page = await site.ask("get", "/call?lang=en")

    assert go.headers["location"] == (f"{site.base}/call" if closed else BROWSER_LINK)
    assert (f'href="{site.base}/join/go"' in page.text) is (not closed)


async def test_the_closed_address_records_no_click_and_the_open_one_the_page_it_sent_to(
    demo_settings: Settings,
) -> None:
    async with running(demo_settings, client=JoinClient.EMBEDDED) as site:
        await site.client.get(f"{site.base}/join/go")
        closed = await _kinds(site)
        await set_group(site.client, "demo", embedded_call_webex_option=OtherWayOffer.ALWAYS)
        await site.client.get(f"{site.base}/join/go")
        opened = await _details(site)

    assert EventKind.JOIN_CLICKED not in closed
    assert opened == ["browser"]


async def _kinds(site: Site) -> list[EventKind]:
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        return [e.kind for e in await tx.get_booking_events(site.booking_id)]


async def _details(site: Site) -> list[str]:
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.get_booking_events(site.booking_id)
    return [e.detail for e in events if e.kind is EventKind.JOIN_CLICKED]
