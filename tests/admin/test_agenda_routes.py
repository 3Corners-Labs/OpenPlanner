"""Agenda with agents and shortfalls in the admin portal (ADR 0044)."""

from datetime import date, time

import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.planning import PlanningService
from app.domain.scheduling import ScheduleOverride, TimeWindow
from app.domain.services import BookingService
from tests.admin.conftest import AUDITOR, GROUP_ADMIN, OUTSIDER, SUPER, Site
from tests.admin.test_call_routes import FRIDAY_0900
from tests.support.domain import ADA, TEST_TOKEN_KEY, book_confirmed

FRIDAY = date(2026, 1, 2)


async def _short_friday(site: Site) -> None:
    """Book 09:00 on Friday twice, then staff Friday 09:00-10:00 with one agent."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    uow = SqlUnitOfWork(app.state.engine)
    bookings = BookingService(uow, site.clock, TEST_TOKEN_KEY)
    for _ in range(2):
        await book_confirmed(
            bookings, group_slug="demo", start=FRIDAY_0900, contact=ADA, locale="nl"
        )
    planning = PlanningService(uow, site.clock, AdminService(uow, site.clock))
    one_agent = TimeWindow(time(9), time(10), 1)
    await planning.add_override(SUPER, "demo", ScheduleOverride(FRIDAY, one_agent))


async def test_the_day_shows_agents_bookings_and_the_shortfall_per_slot(site: Site) -> None:
    await _short_friday(site)
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get("/admin/groups/demo/agenda?day=2026-01-02")).text

    assert 'id="slot-20260102T0800"' in text
    assert "Medewerkers: 1, afspraken: 2" in text
    assert "Tekort: 1" in text
    assert "Medewerkers: 1, afspraken: 0" in text  # 09:30, nobody booked
    assert text.count("<td>Ada") == 2
    assert "Lovelace" not in text


async def test_the_week_is_a_table_of_times_by_days(site: Site) -> None:
    await _short_friday(site)
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get("/admin/groups/demo/agenda/week?week=2026-01-02")).text

    assert "Week van maandag 29 december 2025" in text
    assert '<th scope="row" class="nowrap">09:00–09:30</th>' in text
    assert "/admin/groups/demo/agenda?day=2026-01-02#slot-20260102T0800" in text
    assert "Tijdsloten met meer afspraken dan medewerkers deze week: 1." in text
    assert "Ada" not in text  # counts only


async def test_group_pages_and_dashboard_warn_about_shortfalls(site: Site) -> None:
    await _short_friday(site)
    await site.login_as(GROUP_ADMIN)

    overview = (await site.client.get("/admin/groups/demo")).text
    staffing = (await site.client.get("/admin/groups/demo/staffing")).text
    dashboard = (await site.client.get("/admin/")).text

    notice = "Tijdsloten met meer afspraken dan medewerkers in de komende 14 dagen: 1."
    assert notice in overview
    assert notice in staffing
    assert "Eerste tekort: vrijdag 2 januari 2026, 09:00" in overview
    assert "Tekorten: 1" in dashboard


async def test_without_bookings_view_there_is_no_shortfall_notice(site: Site) -> None:
    await _short_friday(site)
    await site.login_as(AUDITOR)

    overview = await site.client.get("/admin/groups/demo")

    assert overview.status_code == 200
    assert "Tekort" not in overview.text


async def test_the_english_week_view(site: Site) -> None:
    await _short_friday(site)
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get("/admin/groups/demo/agenda/week?week=2026-01-02&lang=en")).text

    assert "Week of Monday, December 29, 2025" in text
    assert "Short: 1" in text


async def test_outsiders_and_auditors_see_no_week(site: Site) -> None:
    for principal in (OUTSIDER, AUDITOR):
        await site.login_as(principal)
        response = await site.client.get("/admin/groups/demo/agenda/week")
        assert response.status_code == 403


@pytest.mark.parametrize("week", ["nope", "0001-01-01", "9999-12-31"])
async def test_an_invalid_week_falls_back_to_this_week(site: Site, week: str) -> None:
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"/admin/groups/demo/agenda/week?week={week}")).text

    assert "Week van maandag 29 december 2025" in text  # NOW is Thursday 1 January 2026


async def test_a_day_at_the_edge_of_the_calendar_falls_back_to_today(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get("/admin/groups/demo/agenda?day=0001-01-01")

    assert response.status_code == 200
    assert "donderdag 1 januari 2026" in response.text
