"""Direct calls for citizens (ADR 0019): start, waiting page, honest when no agent comes."""

import re
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.calls import EventKind
from app.domain.integration import IntegrationService
from app.domain.ports import IntegrationError
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo
from tests.support.domain import NOW

SAME_ORIGIN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
ADA = {"name": "Ada Lovelace", "email": "ada@example.org", "phone": ""}


async def _configure(database_url: str, **changes: object) -> None:
    engine = create_engine(database_url)
    async with SqlUnitOfWork(engine).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        await tx.save_group(replace(group, **changes))
    await engine.dispose()


WIRED = {
    "direct_call_enabled": True,
    "video_host_email": "host@example.nl",
    "wxcc_business_address": "openplanner@example.biz",
    "wxcc_channel": "openplanner-video",
}


class Site:
    def __init__(
        self,
        client: httpx.AsyncClient,
        clock: FixedClock,
        video: FakeVideo,
        dispatcher: FakeDispatcher,
        database_url: str,
    ) -> None:
        self.client, self.clock, self.video, self.dispatcher = client, clock, video, dispatcher
        self.database_url = database_url

    async def start(self) -> httpx.Response:
        return await self.client.post("/g/demo/direct", data=ADA, headers=SAME_ORIGIN)

    async def record(self, task_id: str, kind: EventKind) -> None:
        engine = create_engine(self.database_url)
        uow = SqlUnitOfWork(engine)
        service = IntegrationService(uow, self.clock, AdminService(uow, self.clock))
        await service.record_task_event(task_id, kind, self.clock.now())
        await engine.dispose()


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[Site]:
    await _configure(demo_settings.database_url, **WIRED)
    clock, video, dispatcher = FixedClock(NOW), FakeVideo(), FakeDispatcher()
    integrations = Integrations(None, video, dispatcher)
    async with app_client(demo_settings, clock, integrations) as client:
        yield Site(client, clock, video, dispatcher, demo_settings.database_url)


async def test_group_page_offers_a_direct_call_while_open(site: Site) -> None:
    text = (await site.client.get("/g/demo")).text

    assert 'href="/g/demo/direct"' in text
    assert "Nu een gesprek starten" in text


async def test_starting_creates_meeting_and_work_item_at_once(site: Site) -> None:
    response = await site.start()

    assert response.status_code == 303
    assert re.fullmatch(r"/b/[A-Za-z0-9_-]+/wait", response.headers["location"])
    assert site.video.created[0].start == NOW
    item = site.dispatcher.items[0]
    assert (item.kind, item.first_name, item.meeting_link) == (
        "direct",
        "Ada",
        "https://site.webex.com/m/1",
    )
    page = (await site.client.get(response.headers["location"])).text
    assert "We zoeken een medewerker" in page
    assert 'http-equiv="refresh" content="15"' in page
    assert "/join/go" in page  # the citizen can wait in the lobby right away


async def test_waiting_page_follows_the_task(site: Site) -> None:
    wait = (await site.start()).headers["location"]

    await site.record("task-1", EventKind.TASK_CONNECTED)
    accepted = (await site.client.get(wait)).text

    assert "heeft uw gesprek aangenomen" in accepted


async def test_honest_when_no_agent_picks_up(site: Site) -> None:
    wait = (await site.start()).headers["location"]
    site.clock.advance(timedelta(minutes=11))

    page = (await site.client.get(wait)).text

    assert "geen medewerker beschikbaar" in page
    assert 'href="/g/demo"' in page
    assert "/join/go" not in page
    assert "refresh" not in page


async def test_platform_failure_defers_to_the_worker(site: Site) -> None:
    site.video.error = IntegrationError("server_error", retryable=True)

    response = await site.start()

    assert response.status_code == 303
    assert site.dispatcher.items == []
    assert "We zoeken een medewerker" in (await site.client.get(response.headers["location"])).text


async def test_not_offered_when_switched_off(demo_settings: Settings) -> None:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        group = (await client.get("/g/demo")).text
        form = (await client.get("/g/demo/direct")).text
        post = await client.post("/g/demo/direct", data=ADA, headers=SAME_ORIGIN)

    assert 'href="/g/demo/direct"' not in group
    assert "kan op dit moment niet" in form
    assert post.status_code == 409


async def test_honeypot_and_cross_site_are_refused(site: Site) -> None:
    bot = await site.client.post(
        "/g/demo/direct", data={**ADA, "website": "x"}, headers=SAME_ORIGIN
    )
    cross = await site.client.post(
        "/g/demo/direct", data=ADA, headers={"sec-fetch-site": "cross-site"}
    )

    assert (bot.status_code, cross.status_code) == (400, 403)
    assert site.dispatcher.items == []
