"""The citizen's trial call on Webex (ADR 0071): page, opening, the way in, and its limits."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.ports import IntegrationError
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo
from tests.support.citizen import confirm_newest, emailed_token
from tests.support.domain import NOW

FRIDAY_0900 = "2026-01-02T08:00:00Z"
SAME_ORIGIN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
ADA = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.org", "phone": ""}
HOST = "host@example.nl"


async def _configure(database_url: str, **changes: object) -> None:
    engine = create_engine(database_url)
    async with SqlUnitOfWork(engine).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        await tx.save_group(replace(group, **changes))
    await engine.dispose()


class Site:
    def __init__(self, client: httpx.AsyncClient, clock: FixedClock, video: FakeVideo) -> None:
        self.client, self.clock, self.video = client, clock, video
        self.token = ""

    async def booked(self, *, confirm: bool = True) -> "Site":
        """Book, then confirm by the emailed link (ADR 0056), as a citizen does."""
        await self.client.post(
            "/g/demo/book", data={"start": FRIDAY_0900, **ADA}, headers=SAME_ORIGIN
        )
        self.token = await (confirm_newest if confirm else emailed_token)(self.client)
        return self

    async def open(self) -> httpx.Response:
        return await self.client.post(f"/b/{self.token}/test", headers=SAME_ORIGIN)


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[Site]:
    await _configure(demo_settings.database_url, video_host_email=HOST)
    clock, video = FixedClock(NOW), FakeVideo()
    async with app_client(demo_settings, clock, Integrations(None, video, FakeDispatcher())) as c:
        yield await Site(c, clock, video).booked()


async def test_the_page_explains_and_offers_to_open_a_real_webex_meeting(site: Site) -> None:
    response = await site.client.get(f"/b/{site.token}/test")

    assert response.status_code == 200
    text = response.text
    assert "<h1>Proefgesprek</h1>" in text
    assert "echte Webex-vergadering" in text
    assert "Er komt niemand van het team binnen" in text
    assert f'action="/b/{site.token}/test"' in text
    assert "Proefgesprek openen" in text
    assert "Het proefgesprek blijft 30 minuten open." in text
    assert site.video.created == []  # looking creates nothing at Webex


async def test_opening_schedules_the_hosts_meeting_and_shows_the_way_in(site: Site) -> None:
    response = await site.open()
    page = await site.client.get(f"/b/{site.token}/test")

    assert response.status_code == 303
    assert response.headers["location"] == f"/b/{site.token}/test"
    request = site.video.created[0]
    assert (request.title, request.host_email, request.join_before_host) == (
        "Proefgesprek Demo: videogesprek",
        HOST,
        True,
    )
    assert "Uw proefgesprek staat open tot 09:30." in page.text
    assert f'href="/b/{site.token}/test/go"' in page.text
    assert "Proefgesprekken die u nog kunt openen: 2." in page.text


async def test_the_way_in_leads_to_the_meeting_only_while_it_is_open(site: Site) -> None:
    await site.open()

    during = await site.client.get(f"/b/{site.token}/test/go")
    site.clock.advance(timedelta(minutes=31))
    after = await site.client.get(f"/b/{site.token}/test/go")

    assert during.status_code == 303
    assert during.headers["location"] == "https://site.webex.com/m/1"
    assert after.headers["location"] == f"/b/{site.token}/test"


async def test_opening_needs_a_same_origin_post(site: Site) -> None:
    response = await site.client.post(
        f"/b/{site.token}/test",
        headers={"origin": "http://evil.example", "sec-fetch-site": "cross-site"},
    )

    assert response.status_code in (400, 403)
    assert site.video.created == []


async def test_the_limit_is_told_to_the_citizen(site: Site) -> None:
    for _ in range(3):
        await site.open()
        site.clock.advance(timedelta(minutes=31))

    await site.open()
    page = await site.client.get(f"/b/{site.token}/test")

    assert len(site.video.created) == 3
    assert "U heeft het aantal proefgesprekken voor deze afspraak gebruikt." in page.text
    assert "Proefgesprek openen" not in page.text


async def test_a_refusal_by_webex_is_shown_kindly_and_leaves_the_appointment_alone(
    site: Site,
) -> None:
    site.video.error = IntegrationError("rejected_400", retryable=False, reason="not allowed")

    response = await site.open()
    manage = await site.client.get(f"/b/{site.token}")

    assert response.status_code == 503
    assert "Het proefgesprek openen lukt nu niet." in response.text
    assert "not allowed" not in response.text  # the reason is for admins, on the timeline
    assert "Bevestigd" in manage.text


async def test_the_page_is_in_english_on_request(site: Site) -> None:
    text = (await site.client.get(f"/b/{site.token}/test", params={"lang": "en"})).text

    assert "<h1>Trial call</h1>" in text
    assert "Open trial call" in text


async def test_a_group_without_the_switch_offers_no_trial_call(
    demo_settings: Settings,
) -> None:
    await _configure(demo_settings.database_url, video_host_email=HOST, trial_call_enabled=False)
    video = FakeVideo()
    async with app_client(
        demo_settings, FixedClock(NOW), Integrations(None, video, FakeDispatcher())
    ) as client:
        site = await Site(client, FixedClock(NOW), video).booked()
        page = await client.get(f"/b/{site.token}/test")
        manage = await client.get(f"/b/{site.token}")
        await site.open()

    assert "Voor deze afspraak is geen proefgesprek beschikbaar." in page.text
    assert f"/b/{site.token}/test" not in manage.text
    assert video.created == []


async def test_the_booking_and_join_pages_link_to_the_trial_call(site: Site) -> None:
    manage = await site.client.get(f"/b/{site.token}")
    early = await site.client.get(f"/b/{site.token}/join")  # long before the appointment

    assert f'href="/b/{site.token}/test"' in manage.text
    assert "Uw verbinding vooraf uitproberen" in manage.text
    assert f'href="/b/{site.token}/test"' in early.text
    assert "Liever eerst uw verbinding uitproberen?" in early.text


async def test_an_unknown_token_is_a_404(site: Site) -> None:
    for path in ("/b/unknown/test", "/b/unknown/test/go"):
        assert (await site.client.get(path)).status_code == 404
    assert (await site.client.post("/b/unknown/test", headers=SAME_ORIGIN)).status_code == 404


async def test_a_booking_that_awaits_confirmation_has_no_trial_call(site: Site) -> None:
    """ADR 0056: the address is not verified yet, so no Webex meeting is made for it."""
    await site.client.post(
        "/g/demo/book",
        data={"start": "2026-01-02T08:30:00Z", **{**ADA, "email": "b@example.org"}},
        headers=SAME_ORIGIN,
    )
    pending = await emailed_token(site.client)

    page = await site.client.get(f"/b/{pending}/test")
    opened = await site.client.post(f"/b/{pending}/test", headers=SAME_ORIGIN)

    assert page.status_code == 200
    assert "Proefgesprek openen" not in page.text
    assert opened.status_code == 303
    assert site.video.created == []
