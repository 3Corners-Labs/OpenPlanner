"""Agenda and booking timeline in the admin portal (ADR 0010)."""

from dataclasses import replace
from datetime import UTC, datetime

from app.adapters.db.jobs import SqlJobQueue
from app.adapters.db.reports import HEALTH_REPORT, REGISTRATIONS_REPORT, ReportStore
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.registrations import (
    Listing,
    ListingError,
    Reason,
    Registration,
    State,
    listing_to_report,
)
from app.adapters.webhook_selftest import Delivery, Outcome, deliveries_to_report
from app.domain.admin import AdminService
from app.domain.booking import MeetingRef
from app.domain.calls import EventKind
from app.domain.event_context import bind_booking, bind_level, trace_scope
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType
from app.domain.ports import RequestRecord
from app.domain.request_log import RequestEvents
from app.domain.services import BookingService
from tests.admin.conftest import AUDITOR, GROUP_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.support.domain import ADA, TEST_TOKEN_KEY, book_confirmed

FRIDAY_0900 = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)  # demo group: open weekdays


async def _book(site: Site, slug: str) -> int:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    uow = SqlUnitOfWork(app.state.engine)
    receipt = await book_confirmed(
        BookingService(uow, site.clock, TEST_TOKEN_KEY),
        group_slug=slug,
        start=FRIDAY_0900,
        contact=ADA,
        locale="nl",
    )
    booking_id = receipt.booking.id or 0
    integration = IntegrationService(uow, site.clock, AdminService(uow, site.clock))
    await integration.record(booking_id, EventKind.MEETING_CREATED)
    await integration.record(booking_id, EventKind.TASK_CREATED)
    await integration.record(booking_id, EventKind.TASK_FAILED, detail="ENTRY_POINT_NOT_FOUND")
    return booking_id


async def test_agenda_shows_the_day_with_status_in_words(site: Site) -> None:
    booking_id = await _book(site, "demo")
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get("/admin/groups/demo/agenda?day=2026-01-02")).text

    assert "vrijdag 2 januari 2026" in text
    assert "<td>Ada" in text
    assert "Lovelace" not in text  # first name only
    assert "ada@example.org" not in text
    assert "Meeting klaar" in text
    assert "Taak mislukt" in text
    assert f'href="/admin/groups/demo/bookings/{booking_id}"' in text


async def test_timeline_lists_events_with_reason_codes(site: Site) -> None:
    booking_id = await _book(site, "demo")
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text

    assert f"OP-{booking_id:06d}" in text
    assert "Videomeeting aangemaakt" in text
    assert "Taak in de wachtrij van het contactcenter gezet" in text
    assert "<code>ENTRY_POINT_NOT_FOUND</code>" in text


async def test_a_failed_meeting_explains_what_to_check(site: Site) -> None:
    booking_id = await _book(site, "demo")
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    uow = SqlUnitOfWork(app.state.engine)
    integration = IntegrationService(uow, site.clock, AdminService(uow, site.clock))
    await integration.record(
        booking_id,
        EventKind.MEETING_FAILED,
        detail="rejected_400",
        message="timezone 'Europe/Amsterdam' and timezone offset in '+00:00' do not match",
    )
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text

    assert "Videomeeting aanmaken mislukt" in text
    assert "<code>rejected_400</code>" in text
    assert "Webex-host" in text  # the hint names the field to check
    # ADR 0065: Webex's own words, escaped, under the event.
    assert "Antwoord van Webex: timezone &#39;Europe/Amsterdam&#39;" in text


async def test_an_unknown_code_still_gets_a_hint(site: Site) -> None:
    booking_id = await _book(site, "demo")
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}?lang=en")).text

    assert "<code>ENTRY_POINT_NOT_FOUND</code>" in text
    assert "Webex Contact Center did not accept the task" in text


async def test_a_booking_cannot_be_opened_through_another_groups_url(site: Site) -> None:
    other = await _book(site, "andere-team")
    await site.login_as(SUPER)

    response = await site.client.get(f"/admin/groups/demo/bookings/{other}")

    assert response.status_code == 404


async def test_outsiders_see_neither_agenda_nor_timeline(site: Site) -> None:
    booking_id = await _book(site, "demo")
    await site.login_as(OUTSIDER)

    agenda = await site.client.get("/admin/groups/demo/agenda")
    detail = await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")

    assert (agenda.status_code, detail.status_code) == (403, 403)


async def test_an_invalid_day_falls_back_to_today(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get("/admin/groups/demo/agenda?day=not-a-date")

    assert response.status_code == 200


async def test_integrations_page_shows_status_without_secrets(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/integrations")).text

    assert "Koppelingen" in text
    assert "/webhooks/webex" in text
    assert "Geen geheim ingesteld" in text
    assert "Niet gekoppeld" in text


async def test_integrations_page_shows_the_webhook_base_url(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    app.state.settings = app.state.settings.model_copy(
        update={"webhook_base_url": "https://abc.trycloudflare.com"}
    )
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/integrations")).text

    assert "https://abc.trycloudflare.com/webhooks/webex" in text
    assert "https://abc.trycloudflare.com/webhooks/wxcc" in text


async def test_integrations_page_says_when_nothing_was_checked_yet(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/integrations")).text

    assert "Nog niet gecontroleerd. Voer python -m app.cli webhooks-list uit" in text
    assert "python -m app.cli webhooks-health" in text


async def test_integrations_page_shows_the_last_checks_read_only(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    reports = ReportStore(app.state.engine)
    tunnel = "https://old.trycloudflare.com"
    listing = Listing(
        [
            Registration(
                "webex",
                "w1",
                "meetings/started",
                f"{tunnel}/webhooks/webex",
                "hostEmail=host-demo@example.nl",
                "active",
                True,
                State.STALE,
                Reason.OTHER_TARGET,
            ),
            Registration(
                "wxcc",
                "s1",
                "task events",
                "https://abc.example/webhooks/wxcc",
                "",
                "-",
                None,
                State.CURRENT,
                Reason.NONE,
            ),
        ],
        missing=["webex meetings/ended [host-demo@example.nl]"],
        errors=[ListingError("webex", "rejected_403")],
    )
    await reports.put(
        REGISTRATIONS_REPORT, listing_to_report(listing) | {"base": tunnel}, FRIDAY_0900
    )
    deliveries = [Delivery("webex", True, 530, Outcome.PROXY_ERROR)]
    await reports.put(
        HEALTH_REPORT, deliveries_to_report(deliveries) | {"base": tunnel}, FRIDAY_0900
    )
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/integrations")).text

    assert "Verouderd" in text
    assert "Verwijst naar een ander adres" in text
    assert "Actueel" in text
    assert "<code>hostEmail=host-demo@example.nl</code>" in text
    assert "webex meetings/ended [host-demo@example.nl]" in text
    assert "De lijst van webex kon niet worden opgehaald (rejected_403)." in text
    assert "Fout in proxy of tunnel" in text
    assert "<td>530</td>" in text
    assert "<form" not in text.split("registrations-heading", 1)[1].split("groups-heading", 1)[0]


async def test_timeline_explains_a_work_item_that_could_not_be_ended(site: Site) -> None:
    booking_id = await _book(site, "demo")
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    uow = SqlUnitOfWork(app.state.engine)
    integration = IntegrationService(uow, site.clock, AdminService(uow, site.clock))
    await integration.record(booking_id, EventKind.END_NOT_POSSIBLE, detail="rejected_403")
    await integration.record(booking_id, EventKind.TASK_MARKED_CANCELLED)
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text

    assert "Taak kon niet worden beëindigd" in text
    assert "<code>rejected_403</code>" in text
    assert "Taak als geannuleerd gemarkeerd" in text
    assert "Beëindig de taak in Agent Desktop" in text


async def _failed_meeting(site: Site) -> int:
    """A booking of ``demo`` (with a Webex host) whose meeting job failed on an older version."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    uow = SqlUnitOfWork(app.state.engine)
    async with uow.locked() as tx:
        demo = await tx.get_group("demo")
        assert demo is not None
        await tx.save_group(replace(demo, video_host_email="host@example.nl"))
    booking_id = await _book(site, "demo")
    queue = SqlJobQueue(app.state.engine)
    [meeting] = await queue.claim_due(site.clock.now(), {JobType.CREATE_VIDEO_ROOM}, set())
    await queue.fail(meeting.id, "rejected_400")
    old = IntegrationService(uow, site.clock, AdminService(uow, site.clock))  # no version
    await old.record(booking_id, EventKind.MEETING_FAILED, detail="rejected_400")
    with trace_scope():
        bind_level("demo", "demo")
        bind_booking(booking_id)
        await RequestEvents(uow, site.clock, "0.4.0-rc.7").record(
            RequestRecord("POST", "/v1/meetings", 400, "rejected_400", "ROUTER_42", 120)
        )
    return booking_id


async def test_the_booking_page_shows_jobs_requests_and_an_honest_hint(site: Site) -> None:
    # ADR 0068: the page of 2026-10-30 said "Webex's answer is above" with nothing above.
    booking_id = await _failed_meeting(site)
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text

    assert "bewaart het antwoord van Webex niet" in text
    assert "het antwoord van Webex staat hierboven" not in text
    assert "versie onbekend" in text
    assert "Achtergrondtaken" in text
    assert "Mislukt" in text
    assert 'name="job" value="create_video_room"' in text  # the retry button
    assert "<code>POST /v1/meetings</code>" in text
    assert "<code>ROUTER_42</code>" in text
    assert "0.4.0-rc.7" in text


async def test_without_bookings_manage_there_is_no_retry_button(site: Site) -> None:
    booking_id = await _failed_meeting(site)
    await site.login_as(AUDITOR)

    response = await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")

    assert 'name="job"' not in response.text


async def test_retry_plans_the_meeting_again_and_says_so(site: Site) -> None:
    booking_id = await _failed_meeting(site)
    await site.login_as(GROUP_ADMIN)
    url = f"/admin/groups/demo/bookings/{booking_id}/retry"

    response = await site.client.post(url, data={"job": "create_video_room"}, headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert "done=retried" in response.headers["location"]
    page = (await site.client.get(response.headers["location"])).text
    assert "De taak is opnieuw gepland" in page
    assert 'name="job" value="create_video_room"' not in page  # pending now: no second button
    again = await site.client.post(url, data={"job": "send_confirmation"}, headers=SAME_ORIGIN)
    assert "done=retry_not_possible" in again.headers["location"]


async def _with_meeting(site: Site, booking_id: int, meeting_id: str) -> None:
    """Give the booking the Webex meeting ``meeting_id``, as the worker would."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        await tx.save_meeting(booking_id, MeetingRef(meeting_id, "https://site.webex.com/m/1"))


async def test_the_meeting_id_is_on_the_agenda_and_the_booking_page(site: Site) -> None:
    """A planner can quote the meeting id to Webex support, also after the participant left."""
    booking_id = await _book(site, "demo")
    await _with_meeting(site, booking_id, "meeting-abc123")
    await site.login_as(GROUP_ADMIN)

    agenda = (await site.client.get("/admin/groups/demo/agenda?day=2026-01-02")).text
    detail = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text
    english = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}?lang=en")).text

    assert '<code class="muted">meeting-abc123</code>' in agenda
    assert "<dt>Meeting-ID</dt><dd><code>meeting-abc123</code></dd>" in detail
    assert "<dt>Meeting ID</dt>" in english
    assert "https://site.webex.com/m/1" not in agenda + detail  # the join link stays out


async def test_a_booking_without_a_meeting_shows_no_meeting_id(site: Site) -> None:
    booking_id = await _book(site, "demo")
    await site.login_as(GROUP_ADMIN)

    detail = (await site.client.get(f"/admin/groups/demo/bookings/{booking_id}")).text

    assert "Meeting-ID" not in detail
