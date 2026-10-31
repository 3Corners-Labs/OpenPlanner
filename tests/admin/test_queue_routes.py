"""The own queue in the admin portal (ADR 0043)."""

import re

from app.domain.admin import AdminService
from app.domain.authorization import Principal, Role, Scope
from app.domain.calls import EventKind
from app.domain.integration import IntegrationService
from app.web.static_assets import ASSETS
from tests.admin.conftest import AGENT, GROUP_ADMIN, SAME_ORIGIN, Site
from tests.support.queue import own_queue as _own_queue
from tests.support.queue import site_uow as _uow

QUEUE = "/admin/groups/demo/queue"
COLLEAGUE = Principal(
    "sub-colleague", "Cas Collega", frozenset({Role.AGENT}), frozenset(), frozenset({"demo"})
)


async def _record(site: Site, booking_id: int, kind: EventKind, detail: str = "") -> None:
    """Put ``kind`` on the booking's timeline, as the worker or a Webex webhook would."""
    uow = _uow(site)
    service = IntegrationService(uow, site.clock, AdminService(uow, site.clock))
    await service.record(booking_id, kind, detail=detail)


def _poll_url(page: str) -> str | None:
    """Return the fragment URL the page polls, or ``None`` when it stopped polling."""
    found = re.search(r'data-poll="([^"]+)"', page)
    return found.group(1).replace("&amp;", "&") if found else None


async def test_the_queue_shows_first_names_and_hides_the_meeting_link(site: Site) -> None:
    await _own_queue(site)
    await site.login_as(AGENT)

    overview = (await site.client.get("/admin/groups/demo")).text
    text = (await site.client.get(QUEUE)).text

    assert f'href="{QUEUE}"' in overview  # the tab
    assert "<td>Ada<br>" in text
    assert "Lovelace" not in text
    assert "ada@example.org" not in text
    assert "webex.example" not in text
    assert "Volgende oppakken" in text
    assert 'aria-live="polite"' in text


async def test_pick_up_next_opens_the_meeting_for_that_agent_only(site: Site) -> None:
    booking_id = await _own_queue(site)
    await site.login_as(AGENT)

    picked = await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)
    page = (await site.client.get(f"{QUEUE}/{booking_id}")).text
    await site.login_as(COLLEAGUE)
    again = await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)
    other = await site.client.get(f"{QUEUE}/{booking_id}")

    assert picked.status_code == 303
    assert picked.headers["location"] == f"{QUEUE}/{booking_id}"
    assert 'href="https://webex.example/j/1"' in page
    assert again.status_code == 409
    assert "Er is geen afspraak meer om op te pakken." in again.text
    assert other.status_code == 409
    assert "webex.example" not in other.text
    async with _uow(site).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=5)
    assert any(e.event.detail == "queue.claimed" for e in events)


async def test_a_group_admin_releases_a_colleagues_claim(site: Site) -> None:
    booking_id = await _own_queue(site)
    await site.login_as(AGENT)
    await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)
    await site.login_as(GROUP_ADMIN)

    listing = (await site.client.get(QUEUE)).text
    released = await site.client.post(f"{QUEUE}/{booking_id}/release", headers=SAME_ORIGIN)

    assert "Opgepakt door een collega" in listing
    assert f'action="{QUEUE}/{booking_id}/release"' in listing
    assert released.status_code == 303
    assert "Volgende oppakken" in (await site.client.get(QUEUE)).text


async def test_a_chosen_row_only_with_free_pick(site: Site) -> None:
    booking_id = await _own_queue(site)
    await site.login_as(AGENT)

    refused = await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)

    assert refused.status_code == 409
    assert "altijd de volgende" in refused.text
    assert f'action="{QUEUE}/{booking_id}/claim"' not in refused.text


async def test_free_pick_shows_a_button_per_row(site: Site) -> None:
    booking_id = await _own_queue(site, queue_free_pick_allowed=True)
    await site.login_as(AGENT)

    listing = (await site.client.get(QUEUE)).text
    claimed = await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)

    assert f'action="{QUEUE}/{booking_id}/claim"' in listing
    assert claimed.status_code == 303


async def test_the_list_refreshes_through_a_fragment(site: Site) -> None:
    """ADR 0072: the page polls a fragment; 204 while nothing changed; never a page reload."""
    await _own_queue(site)
    await site.login_as(AGENT)

    page = (await site.client.get(QUEUE)).text
    url = _poll_url(page)
    assert url is not None
    assert url.startswith(f"{QUEUE}/live?seen=")
    unchanged = await site.client.get(url)
    changed = await site.client.get(f"{QUEUE}/live?seen=something-else")

    assert f'<script src="{ASSETS.url("live-region.js")}" defer></script>' in page
    assert 'data-poll-seconds="10"' in page
    assert "http-equiv" not in page
    assert unchanged.status_code == 204
    assert changed.status_code == 200
    assert "data-live-fragment" in changed.text
    assert "<td>Ada<br>" in changed.text
    assert "webex.example" not in changed.text
    assert "<html" not in changed.text


async def test_a_failed_video_link_is_said_on_the_row_and_after_the_claim(site: Site) -> None:
    """ADR 0072: Webex refused the meeting; the agent reads it instead of "no meeting"."""
    booking_id = await _own_queue(site, meeting=False)
    await _record(site, booking_id, EventKind.MEETING_FAILED, "rejected_400")
    await site.login_as(AGENT)

    rows = (await site.client.get(QUEUE)).text
    picked = await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)
    claimed = (await site.client.get(picked.headers["location"])).text

    assert "Videolink mislukt" in rows
    assert "kon niet worden klaargezet" in claimed
    assert "Deelnemen aan het gesprek" not in claimed
    assert "Vrijgeven" in claimed
    assert "rejected_400" not in claimed  # the reason stays on the timeline (ADR 0065)
    assert _poll_url(claimed) is None  # nothing will change any more


async def test_the_claimed_page_follows_the_citizen(site: Site) -> None:
    booking_id = await _own_queue(site)
    await site.login_as(AGENT)
    picked = await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)
    page = (await site.client.get(picked.headers["location"])).text
    url = _poll_url(page)
    assert url is not None

    same = await site.client.get(url)
    await _record(site, booking_id, EventKind.GUEST_JOINED)
    lobby = await site.client.get(url)

    assert "Deelnemer nog niet in het gesprek" in page
    assert "Deelnemen aan het gesprek" in page
    assert same.status_code == 204
    assert lobby.status_code == 200
    assert "wacht in de wachtruimte" in lobby.text
    assert "webex.example" in lobby.text  # the claimer's link travels with the fragment


async def test_a_pending_video_link_is_said_while_the_worker_is_on_it(site: Site) -> None:
    await _own_queue(site, meeting=False)
    await site.login_as(AGENT)

    rows = (await site.client.get(QUEUE)).text

    assert "Videolink wordt klaargezet" in rows


async def test_a_released_claim_is_told_on_the_open_page(site: Site) -> None:
    """ADR 0072: a group admin released the claim; the agent's page says so and stops polling."""
    booking_id = await _own_queue(site)
    await site.login_as(AGENT)
    picked = await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)
    url = _poll_url((await site.client.get(picked.headers["location"])).text)
    assert url is not None
    async with _uow(site).locked() as tx:
        await tx.release_claim(booking_id)

    lost = await site.client.get(url)

    assert lost.status_code == 200
    assert "data-live-fragment" in lost.text
    assert _poll_url(lost.text) is None
    assert "webex.example" not in lost.text
    assert f'href="{QUEUE}"' in lost.text
