"""The retention overview page (ADR 0037): counts per group, never personal data."""

from datetime import UTC, datetime, timedelta

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.services import BookingService
from tests.admin.conftest import AUDITOR, ORG_ADMIN, OUTSIDER, SUPER, Site
from tests.support.domain import ADA, TEST_TOKEN_KEY

FRIDAY_0900 = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)  # demo group: open weekdays


async def _book(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    await BookingService(SqlUnitOfWork(app.state.engine), site.clock, TEST_TOKEN_KEY).book(
        group_slug="demo", start=FRIDAY_0900, contact=ADA, locale="nl"
    )


async def test_a_booking_past_its_purge_moment_needs_attention(site: Site) -> None:
    await _book(site)
    site.clock.advance(timedelta(days=4))  # the purge job never ran: no worker in this test
    await site.login_as(AUDITOR)

    text = (await site.client.get("/admin/retention")).text

    assert '<h1 id="page-title">Bewaartermijnen</h1>' in text
    assert "Afspraken met persoonsgegevens over de bewaartermijn: 1." in text
    assert '<span class="chip chip--danger">Actie nodig</span>' in text
    # Counts only: nothing of the citizen reaches the page, not even for the auditor.
    for pii in ("Ada", "ada@example.org", "06 1234 5678"):
        assert pii not in text
    assert 'href="/admin/retention"' in text


async def test_an_upcoming_booking_is_in_order(site: Site) -> None:
    await _book(site)
    await site.login_as(ORG_ADMIN)

    text = (await site.client.get("/admin/retention")).text

    assert "In orde: er zijn geen persoonsgegevens over hun bewaartermijn" in text
    assert "Actie nodig" not in text
    assert "Nog niet" in text  # nothing purged yet


async def test_each_admin_sees_the_groups_of_their_own_organisation(site: Site) -> None:
    await site.login_as(OUTSIDER)
    other = (await site.client.get("/admin/retention")).text
    await site.login_as(SUPER)
    everything = (await site.client.get("/admin/retention")).text

    assert "Andere" in other
    assert "Demo" not in other.split('id="page-title"')[1]
    assert "Andere" in everything
    assert "Demo" in everything
