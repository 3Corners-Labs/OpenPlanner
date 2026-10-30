"""The agenda's live meeting status: refresh on open, a polled fragment, no JavaScript needed
(ADR 0010, 0072)."""

import re
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.calls import CallState, EventKind, MeetingStatus
from app.domain.integration import IntegrationService
from app.domain.ports import IntegrationError
from tests.admin.conftest import AUDITOR, GROUP_ADMIN, OUTSIDER, Site
from tests.fakes.integrations import FakeVideo
from tests.support.queue import FRIDAY_0900
from tests.support.queue import own_queue as _own_queue
from tests.support.queue import site_uow as _uow

AGENDA = "/admin/groups/demo/agenda?day=2026-01-02"
LIVE = "/admin/groups/demo/agenda/live?day=2026-01-02"
RUNNING = MeetingStatus(
    CallState.IN_PROGRESS, participants=2, host_joined=True, started_at=FRIDAY_0900
)


async def _meeting(site: Site) -> int:
    """Book Friday 09:00 with a meeting that was created, five minutes before its start."""
    booking_id = await _own_queue(site, video_host_email="host@example.nl")
    uow = _uow(site)
    await IntegrationService(uow, site.clock, AdminService(uow, site.clock)).record(
        booking_id, EventKind.MEETING_CREATED, FRIDAY_0900 - timedelta(days=1)
    )
    return booking_id


def _poll_url(page: str) -> str | None:
    found = re.search(r'data-poll="([^"]+)"', page)
    return found.group(1).replace("&amp;", "&") if found else None


async def test_opening_the_agenda_asks_the_provider_when_no_webhook_came(
    video_site: Site, video: FakeVideo
) -> None:
    await _meeting(video_site)
    video.statuses["m-1"] = RUNNING
    await video_site.login_as(GROUP_ADMIN)

    text = (await video_site.client.get(AGENDA)).text

    assert video.polled == [("m-1", "host@example.nl")]
    assert "Gesprek bezig" in text
    assert "Status bijgewerkt: 1 gesprekken bezig, 0 niet opgepakt." in text


async def test_without_webex_the_agenda_shows_what_webhooks_recorded(site: Site) -> None:
    await _meeting(site)
    await site.login_as(GROUP_ADMIN)

    page = await site.client.get(AGENDA)

    assert page.status_code == 200
    assert "Meeting klaar" in page.text


async def test_the_agenda_refreshes_through_a_fragment_and_works_without_the_script(
    video_site: Site, video: FakeVideo
) -> None:
    await _meeting(video_site)
    await video_site.login_as(GROUP_ADMIN)

    page = await video_site.client.get(AGENDA)
    url = _poll_url(page.text)
    assert url is not None
    assert url.startswith("/admin/groups/demo/agenda/live?day=2026-01-02&seen=")
    unchanged = await video_site.client.get(url)
    video_site.clock.advance(timedelta(seconds=31))
    video.statuses["m-1"] = RUNNING
    changed = await video_site.client.get(url)

    # The page: the script is allowed on this page only, the status is there without it,
    # and a link refreshes it.
    assert '<script src="/static/live-region.js" defer></script>' in page.text
    assert 'data-poll-seconds="30"' in page.text  # MEETING_STATUS_REFRESH_SECONDS, class C
    assert "http-equiv" not in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "Meeting klaar" in page.text
    assert 'href="?day=2026-01-02">Vernieuwen</a>' in page.text
    # The poll: 204 while the table is the same, the table (no page shell) once it changed.
    assert unchanged.status_code == 204
    assert changed.status_code == 200
    assert "Gesprek bezig" in changed.text
    assert "<html" not in changed.text
    assert "data-live-fragment" in changed.text
    assert 'aria-live="polite"' in page.text


async def test_polling_does_not_hammer_the_provider(video_site: Site, video: FakeVideo) -> None:
    await _meeting(video_site)
    await video_site.login_as(GROUP_ADMIN)

    for _ in range(5):  # five open pages, or one that polls faster than the window
        await video_site.client.get(LIVE + "&seen=x")

    assert len(video.polled) == 1


async def test_a_refusal_by_the_provider_reaches_the_timeline_with_its_reason(
    video_site: Site, video: FakeVideo
) -> None:
    booking_id = await _meeting(video_site)
    video.status_error = IntegrationError(
        "rejected_403", retryable=False, reason="The host is not licensed for this site"
    )
    await video_site.login_as(GROUP_ADMIN)

    agenda = await video_site.client.get(AGENDA)
    timeline = (await video_site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text

    assert agenda.status_code == 200  # a failing provider never breaks the page
    assert "Meeting klaar" in agenda.text
    assert "<code>polled:rejected_403</code>" in timeline
    assert "The host is not licensed for this site" in timeline


async def test_a_day_in_the_past_does_not_poll(video_site: Site, video: FakeVideo) -> None:
    await _meeting(video_site)
    await video_site.login_as(GROUP_ADMIN)

    page = (await video_site.client.get("/admin/groups/demo/agenda?day=2025-12-01")).text

    assert "data-live-fragment" in page
    assert _poll_url(page) is None


async def test_the_fragment_has_no_personal_data(video_site: Site, video: FakeVideo) -> None:
    await _meeting(video_site)
    video.statuses["m-1"] = RUNNING
    await video_site.login_as(GROUP_ADMIN)

    text = (await video_site.client.get(LIVE + "&seen=x")).text

    assert "<td>Ada" in text
    for private in ("Lovelace", "ada@example.org", "webex.example", "host@example.nl"):
        assert private not in text


@pytest.mark.parametrize("principal", [OUTSIDER, AUDITOR])
async def test_the_fragment_is_refused_without_the_page_permission(
    video_site: Site, video: FakeVideo, principal: object
) -> None:
    await _meeting(video_site)
    await video_site.login_as(principal)  # type: ignore[arg-type]

    refused = await video_site.client.get(LIVE + "&seen=x")

    assert refused.status_code == 403
    assert video.polled == []  # a refusal asks nothing of the provider


async def test_the_fragment_needs_a_session(video_site: Site) -> None:
    redirected = await video_site.client.get(LIVE + "&seen=x")

    assert redirected.status_code in (303, 401)
