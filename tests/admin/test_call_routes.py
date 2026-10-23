"""Agenda and booking timeline in the admin portal (ADR 0010)."""

from datetime import UTC, datetime

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.calls import EventKind
from app.domain.integration import IntegrationService
from app.domain.services import BookingService
from tests.admin.conftest import GROUP_ADMIN, OUTSIDER, SUPER, Site
from tests.support.domain import ADA, TEST_TOKEN_KEY

FRIDAY_0900 = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)  # demo group: open weekdays


async def _book(site: Site, slug: str) -> int:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    uow = SqlUnitOfWork(app.state.engine)
    receipt = await BookingService(uow, site.clock, TEST_TOKEN_KEY).book(
        group_slug=slug, start=FRIDAY_0900, contact=ADA, locale="nl"
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
    assert "Taak in de WxCC-wachtrij gezet" in text
    assert "<code>ENTRY_POINT_NOT_FOUND</code>" in text


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
