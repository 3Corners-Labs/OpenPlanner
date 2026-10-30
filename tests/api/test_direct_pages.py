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
from app.domain.booking import Claim
from app.domain.calls import EventKind
from app.domain.integration import IntegrationService
from app.domain.ports import IntegrationError
from app.domain.scheduling import DispatchKind, Offer
from app.web.static_assets import ASSETS
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo
from tests.support.domain import NOW

SAME_ORIGIN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
ADA = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.org", "phone": ""}


async def _configure(database_url: str, **changes: object) -> None:
    """Change the demo group, and let every staffing block offer direct calls (ADR 0072)."""
    engine = create_engine(database_url)
    async with SqlUnitOfWork(engine).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        await tx.save_group(replace(group, **changes))
        rules, _overrides = await tx.get_schedule_items(group.id)
        for rule in rules:
            block = replace(rule.window, offer=Offer.BOTH)
            await tx.update_schedule_rule(group.id, rule.id or 0, replace(rule, window=block))
    await engine.dispose()


WIRED = {
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

    async def latest_booking_id(self) -> int:
        """Return the id of the direct call started last."""
        engine = create_engine(self.database_url)
        async with SqlUnitOfWork(engine).read() as tx:
            group = await tx.get_group("demo")
            assert group is not None
            calls = await tx.get_direct_calls(group.id, NOW - timedelta(days=1))
        await engine.dispose()
        return max(c.booking_id for c in calls)

    async def record_booking_event(
        self, booking_id: int, kind: EventKind, detail: str = ""
    ) -> None:
        """Put ``kind`` on the booking's timeline, as the worker or a webhook would."""
        engine = create_engine(self.database_url)
        uow = SqlUnitOfWork(engine)
        service = IntegrationService(uow, self.clock, AdminService(uow, self.clock))
        await service.record(booking_id, kind, detail=detail)
        await engine.dispose()

    async def claim(self, booking_id: int) -> None:
        """Let an agent of the own queue pick the call up (ADR 0043)."""
        engine = create_engine(self.database_url)
        async with SqlUnitOfWork(engine).locked() as tx:
            assert await tx.claim_booking(booking_id, Claim(self.clock.now(), "pseudonym"))
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
    assert "http-equiv" not in page  # no automatic reload (WCAG 2.2.1)
    assert "Status verversen" in page
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
    assert "Status verversen" not in page


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


async def test_queue_info_on_the_form_and_the_waiting_page(site: Site) -> None:
    before = (await site.client.get("/g/demo/direct")).text
    wait = (await site.start()).headers["location"]

    after = (await site.client.get("/g/demo/direct")).text
    page = (await site.client.get(wait)).text

    assert "Er wacht op dit moment niemand." in before
    assert "Aantal mensen dat nu wacht: 1." in after
    assert "Uw plaats in de rij: 1." in page
    assert "Meestal neemt" not in page  # no pickups yet: no estimate


async def test_full_when_every_agent_is_taken(site: Site) -> None:
    await _configure(site.database_url, direct_call_max_concurrent=1)
    await site.start()

    group = (await site.client.get("/g/demo")).text
    form = (await site.client.get("/g/demo/direct")).text
    refused = await site.start()

    assert "Alle medewerkers zijn nu in gesprek" in group
    assert 'href="/g/demo/direct"' not in group
    assert "Alle medewerkers zijn nu in gesprek" in form
    assert 'action="/g/demo/direct"' not in form
    assert refused.status_code == 409
    assert "Alle medewerkers zijn net in gesprek" in refused.text
    assert len(site.dispatcher.items) == 1

    await site.record("task-1", EventKind.TASK_ENDED)

    assert (await site.start()).status_code == 303


def _poll_url(page: str) -> str | None:
    """Return the status fragment's URL the page polls, or ``None`` when it stopped."""
    found = re.search(r'data-poll="([^"]+)"', page)
    return found.group(1).replace("&amp;", "&") if found else None


async def test_the_page_polls_a_status_fragment_without_reloading(site: Site) -> None:
    """ADR 0072: live through a fragment; 204 while nothing changed; never a page reload."""
    wait = (await site.start()).headers["location"]

    page = (await site.client.get(wait)).text
    url = _poll_url(page)
    assert url is not None
    unchanged = await site.client.get(url)
    await site.record("task-1", EventKind.TASK_CONNECTED)
    changed = await site.client.get(url)

    assert f'<script src="{ASSETS.url("live-region.js")}" defer></script>' in page
    assert "http-equiv" not in page
    assert 'data-poll-seconds="10"' in page  # QUEUE_REFRESH_SECONDS, class C
    assert unchanged.status_code == 204
    assert changed.status_code == 200
    assert "data-live-fragment" in changed.text
    assert "heeft uw gesprek aangenomen" in changed.text
    assert "<html" not in changed.text


async def test_a_claim_in_the_own_queue_shows_as_accepted(site: Site) -> None:
    """ADR 0072: the own queue has no WxCC task; the claim tells the citizen an agent is coming."""
    await _configure(site.database_url, dispatch_kind=DispatchKind.OPENPLANNER_QUEUE)
    wait = (await site.start()).headers["location"]
    searching = (await site.client.get(wait)).text

    await site.claim(await site.latest_booking_id())
    accepted = (await site.client.get(wait)).text
    await site.record_booking_event(await site.latest_booking_id(), EventKind.HOST_JOINED)
    in_call = (await site.client.get(wait)).text

    assert site.dispatcher.items == []  # no task for the own queue
    assert "We zoeken een medewerker" in searching
    assert "wachtruimte gaan" in searching  # the meeting is ready: wait in the lobby
    assert "heeft uw gesprek aangenomen" in accepted
    assert "/join/go" in accepted
    assert "De medewerker is in het gesprek" in in_call
    assert _poll_url(in_call) is None  # nothing left to wait for


async def test_nobody_picking_up_in_the_own_queue_is_said_honestly(site: Site) -> None:
    await _configure(site.database_url, dispatch_kind=DispatchKind.OPENPLANNER_QUEUE)
    wait = (await site.start()).headers["location"]
    site.clock.advance(timedelta(minutes=11))

    page = (await site.client.get(wait)).text

    assert "geen medewerker beschikbaar" in page
    assert 'href="/g/demo"' in page


async def test_a_failed_meeting_is_told_instead_of_waiting_for_nothing(site: Site) -> None:
    """ADR 0072: Webex refused the meeting (ADR 0065 reason on the timeline): say so."""
    site.video.error = IntegrationError("rejected_400", retryable=False)
    wait = (await site.start()).headers["location"]
    preparing = (await site.client.get(wait)).text

    await site.record_booking_event(
        await site.latest_booking_id(), EventKind.MEETING_FAILED, "rejected_400"
    )
    failed = (await site.client.get(wait)).text

    assert "We zetten uw videogesprek klaar" in preparing
    assert "/join/go" not in preparing
    assert "technische storing" in failed
    assert 'href="/g/demo"' in failed  # schedule instead
    assert "/join/go" not in failed
    assert _poll_url(failed) is None
