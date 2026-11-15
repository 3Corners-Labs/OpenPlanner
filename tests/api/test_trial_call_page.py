"""The trial call on the call page (ADR 0123 point 9, step 6b) and its abuse cases.

For a service that uses the call page the trial call opens there: the trial meeting (no booking's
own) is entered through the page, with a guest token the server hands out only while the trial
call is open. Each case of the threat model's flow section for this step has a test here: a
service that did not choose the page, the switch off, no session, another browser, a trial call
that is not open, a cross-site post, the way around the page, and the words that never name the
video platform. A service that did not choose the page keeps its trial call as before
(``tests/api/test_trial_pages.py``).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.adapters.db.models import BookingRow
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.call_page import OtherWayOffer
from app.domain.calls import EventKind
from app.domain.scheduling import JoinClient
from app.web.security_policy import CALL_CSP
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo, webex_cc
from tests.support.call_page import Page, set_group
from tests.support.domain import NOW
from tests.support.participant import SAME_ORIGIN, book, confirm_newest, open_link

HOST = "host@example.nl"
CROSS_SITE = {"origin": "https://elsewhere.example", "sec-fetch-site": "cross-site"}
TRIAL_LINK = "https://site.webex.com/m/1"
SUPPLIER = ("webex", "cisco")


@dataclass
class Site:
    """The app with a confirmed booking of Ada in the demo group and a fake video provider."""

    client: httpx.AsyncClient
    video: FakeVideo
    clock: FixedClock
    base: str

    async def open_trial(self) -> httpx.Response:
        """Open the trial call as the trial page's form does."""
        return await self.client.post(f"{self.base}/test", headers=SAME_ORIGIN)

    def stranger(self) -> httpx.AsyncClient:
        """A browser without the participant's cookie."""
        return httpx.AsyncClient(
            transport=self.client._transport,  # type: ignore[attr-defined]  # noqa: SLF001
            base_url="http://testserver",
        )


async def _site(
    settings: Settings, *, client: JoinClient, option: OtherWayOffer | None = None
) -> AsyncIterator[Site]:
    clock, video = FixedClock(NOW), FakeVideo()
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
        base = await open_link(http, await confirm_newest(http))
        yield Site(http, video, clock, base)


@pytest.fixture
def call_settings(demo_settings: Settings) -> Settings:
    """The demo installation with the call page switched on."""
    return demo_settings.model_copy(update={"embedded_call_enabled": True})


@pytest.fixture
async def site(call_settings: Settings) -> AsyncIterator[Site]:
    """A service that uses the call page, its trial call not yet opened."""
    async for each in _site(call_settings, client=JoinClient.EMBEDDED):
        yield each


async def test_the_trial_call_opens_on_the_call_page_with_the_pages_policy(site: Site) -> None:
    await site.open_trial()

    page = await site.client.get(f"{site.base}/test/call")

    assert page.status_code == 200
    assert page.headers["content-security-policy"] == CALL_CSP
    assert page.headers["cache-control"] == "no-store"
    view = Page(page.text)
    root = view.element("data-call")
    assert root.attrs["data-call-token-url"] == f"{site.base}/test/call/token"
    assert root.attrs["data-call-staff-url"] == ""  # nobody waits in a trial call
    assert view.visible_headings() == ["Proefgesprek"]
    assert "Open tot" in view.state("start").text()
    # The way back is the trial page, not the appointment's join page.
    assert f'href="{site.base}/test"' in page.text
    assert f'href="{site.base}/join"' not in page.text


async def test_the_trial_page_leads_to_the_call_page_and_never_names_the_platform(
    site: Site,
) -> None:
    await site.open_trial()

    trial = await site.client.get(f"{site.base}/test?lang=en")
    page = await site.client.get(f"{site.base}/test/call?lang=en")

    assert f'href="{site.base}/test/call"' in trial.text
    assert f'href="{site.base}/test/go"' not in trial.text
    for text in (trial.text, page.text):
        visible = text.lower()
        assert not any(name in visible for name in SUPPLIER)
    assert "nothing to install" in trial.text
    assert trial.headers["content-security-policy"] != CALL_CSP  # the trial page is no call page


async def test_the_trial_meeting_is_created_without_the_platforms_chat(site: Site) -> None:
    await site.open_trial()

    [request] = site.video.created
    assert request.join_before_host is True
    assert request.chat_enabled is False


async def test_a_trial_call_of_a_service_that_did_not_choose_the_page_keeps_the_chat(
    call_settings: Settings,
) -> None:
    async for each in _site(call_settings, client=JoinClient.BROWSER):
        await each.open_trial()

        assert [r.chat_enabled for r in each.video.created] == [True]
        trial = await each.client.get(f"{each.base}/test")
        assert f'href="{each.base}/test/go"' in trial.text
        assert "Webex" in trial.text  # the words of a service that stays on the platform's page


async def test_opening_the_page_is_recorded_on_the_timeline_without_the_link(site: Site) -> None:
    await site.open_trial()

    for _ in range(3):  # a reload loop adds no row: once per trial call
        await site.client.get(f"{site.base}/test/call")

    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with app.state.engine.connect() as conn:
        booking_id = int((await conn.execute(select(BookingRow.id))).scalars().all()[-1])
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.get_booking_events(booking_id)
    joined = [e for e in events if e.kind is EventKind.TRIAL_JOINED]
    assert [e.detail for e in joined] == ["embedded"]
    # A second trial call is a new one: its first opening is recorded again.
    site.clock.advance(timedelta(minutes=31))
    await site.open_trial()
    await site.client.get(f"{site.base}/test/call")
    await site.client.get(f"{site.base}/test/call")
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        again = await tx.get_booking_events(booking_id)
    assert [e.detail for e in again if e.kind is EventKind.TRIAL_JOINED] == ["embedded"] * 2
    assert all(TRIAL_LINK not in (e.detail or "") for e in events)


async def test_the_page_sends_the_participant_back_when_no_trial_call_is_open(
    site: Site,
) -> None:
    before = await site.client.get(f"{site.base}/test/call")
    await site.open_trial()
    site.clock.advance(timedelta(minutes=31))
    after = await site.client.get(f"{site.base}/test/call")

    for refused in (before, after):
        assert refused.status_code == 303
        assert refused.headers["location"] == f"{site.base}/test"


async def test_the_token_is_for_the_trial_meeting_only_while_it_is_open(site: Site) -> None:
    await site.open_trial()

    token = await site.client.post(f"{site.base}/test/call/token", headers=SAME_ORIGIN)

    assert token.status_code == 200
    body = token.json()
    assert body["token"] == "guest-token-1"
    assert body["link"] == TRIAL_LINK  # the trial meeting, no other
    assert token.headers["cache-control"] == "no-store"
    assert len(site.video.guests) == 1
    site.clock.advance(timedelta(minutes=31))
    closed = await site.client.post(f"{site.base}/test/call/token", headers=SAME_ORIGIN)
    assert (closed.status_code, closed.json()) == (409, {"error": "not_open"})
    assert len(site.video.guests) == 1  # a closed trial call mints nothing


async def test_no_token_before_a_trial_call_is_opened(site: Site) -> None:
    token = await site.client.post(f"{site.base}/test/call/token", headers=SAME_ORIGIN)

    assert (token.status_code, token.json()) == (409, {"error": "not_open"})
    assert site.video.guests == []


async def test_without_the_participants_session_nothing_is_handed_out(site: Site) -> None:
    await site.open_trial()
    async with site.stranger() as stranger:
        page = await stranger.get(f"{site.base}/test/call")
        token = await stranger.post(f"{site.base}/test/call/token", headers=SAME_ORIGIN)

    assert page.status_code == token.status_code == 403
    assert "guest-token" not in token.text
    assert TRIAL_LINK not in token.text
    assert site.video.guests == []


async def test_a_cross_site_post_for_a_trial_token_is_refused(site: Site) -> None:
    await site.open_trial()

    response = await site.client.post(f"{site.base}/test/call/token", headers=CROSS_SITE)

    assert response.status_code in (400, 403)
    assert site.video.guests == []


async def test_a_service_that_did_not_choose_the_page_has_no_trial_call_page(
    call_settings: Settings,
) -> None:
    async for each in _site(call_settings, client=JoinClient.CHOICE):
        await each.open_trial()
        page = await each.client.get(f"{each.base}/test/call")
        token = await each.client.post(f"{each.base}/test/call/token", headers=SAME_ORIGIN)

        assert page.status_code == token.status_code == 404
        assert each.video.guests == []


@pytest.mark.parametrize("route", ["get", "post"])
async def test_the_switch_off_closes_the_trial_call_page_too(
    demo_settings: Settings, route: str
) -> None:
    off = demo_settings.model_copy(update={"embedded_call_enabled": False})
    async for each in _site(off, client=JoinClient.EMBEDDED):
        await each.open_trial()
        path = f"{each.base}/test/call" + ("/token" if route == "post" else "")
        headers = SAME_ORIGIN if route == "post" else {}

        response = await getattr(each.client, route)(path, headers=headers)

        assert response.status_code == 404
        # With the switch off a held ``embedded`` is the redirect again: the trial page says so.
        trial = await each.client.get(f"{each.base}/test")
        assert f'href="{each.base}/test/go"' in trial.text


async def test_the_way_around_the_page_is_closed_while_the_services_option_is_hidden(
    site: Site,
) -> None:
    await site.open_trial()

    around = await site.client.get(f"{site.base}/test/go")
    page = await site.client.get(f"{site.base}/test/call")

    assert around.headers["location"] == f"{site.base}/test/call"
    assert "/test/go" not in page.text  # no link to the platform's address


async def test_the_way_around_the_page_opens_when_the_services_option_allows_it(
    call_settings: Settings,
) -> None:
    async for site in _site(call_settings, client=JoinClient.EMBEDDED, option=OtherWayOffer.ALWAYS):
        await site.open_trial()

        around = await site.client.get(f"{site.base}/test/go")
        page = await site.client.get(f"{site.base}/test/call")

        assert around.headers["location"].startswith(TRIAL_LINK)
        assert f'href="{site.base}/test/go"' in page.text


async def test_the_trial_and_the_appointment_share_one_guest(site: Site) -> None:
    """One guest per booking (ADR 0123 point 7): the trial call mints the booking's own subject."""
    await site.open_trial()
    await site.client.post(f"{site.base}/test/call/token", headers=SAME_ORIGIN)
    await site.client.post(f"{site.base}/test/call/token", headers=SAME_ORIGIN)

    subjects = {subject for subject, _ in site.video.guests}

    assert len(subjects) == 1
    assert len(site.video.created) == 1  # the appointment's own meeting is not made here


async def test_the_page_words_of_a_trial_call_never_send_the_participant_to_an_appointment(
    site: Site,
) -> None:
    """The back link says where it leads (2.4.4) and the states a trial can end in are its own."""
    await site.open_trial()

    english = (await site.client.get(f"{site.base}/test/call?lang=en")).text
    dutch = (await site.client.get(f"{site.base}/test/call?lang=nl")).text

    for text, back, not_open, deleted in (
        (
            english,
            "Back to the trial call page",
            "The trial call is no longer open.",
            "The data of this trial call is deleted together with the rest of your appointment",
        ),
        (
            dutch,
            "Terug naar de proefgesprekpagina",
            "Het proefgesprek is niet meer geopend.",
            "De gegevens van dit proefgesprek worden samen met de rest van uw afspraak verwijderd",
        ),
    ):
        assert text.count(f'href="{site.base}/test">{back}</a>') == 3  # left, ended, the foot
        assert not_open in text
        assert deleted in text
        assert "Back to your appointment" not in text
        assert "Terug naar uw afspraak" not in text
        assert "Check the time of your appointment" not in text
