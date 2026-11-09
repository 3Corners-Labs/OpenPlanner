"""The usage reports as an admin sees them: scope, small cells, ranges, words (ADR 0117 step 5)."""

from tests.admin.conftest import AGENT, AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, Site
from tests.support.usage_data import DAY
from tests.support.usage_data import fill as _fill


async def test_a_group_admin_reads_the_own_groups_tables(site: Site) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(f"/admin/groups/demo/usage?from={DAY}&to={DAY}")

    assert response.status_code == 200
    assert "Boekingen gemaakt per dag" in response.text
    assert "Geannuleerd door de planner" in response.text
    assert ">12<" in response.text


async def test_a_small_count_reads_less_than_the_minimum(site: Site) -> None:
    await _fill(site, "demo", 3)
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(f"/admin/groups/demo/usage?from={DAY}&to={DAY}")

    assert "&lt; 5" in response.text
    assert ">3<" not in response.text


async def test_another_organisations_group_is_refused(site: Site) -> None:
    await _fill(site, "andere-team", 12)
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(f"/admin/groups/andere-team/usage?from={DAY}&to={DAY}")

    assert response.status_code == 403
    assert ">12<" not in response.text


async def test_an_organisation_admin_cannot_read_another_organisations_page(
    site: Site,
) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(OUTSIDER)

    page = await site.client.get(f"/admin/organisations/demo/usage?from={DAY}&to={DAY}")
    group = await site.client.get(f"/admin/groups/demo/usage?from={DAY}&to={DAY}")

    assert page.status_code == 403
    assert group.status_code == 403
    assert ">12<" not in page.text + group.text


async def test_the_agent_has_no_reports(site: Site) -> None:
    await site.login_as(AGENT)

    assert (await site.client.get("/admin/reports")).status_code == 403
    assert (await site.client.get("/admin/groups/demo/usage")).status_code == 403


async def test_the_auditor_reads_the_organisation_page(site: Site) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(AUDITOR)

    response = await site.client.get(f"/admin/organisations/demo/usage?from={DAY}&to={DAY}")

    assert response.status_code == 200
    assert ">12<" in response.text


async def test_the_list_shows_a_count_per_group_and_hides_a_small_one(site: Site) -> None:
    await _fill(site, "demo", 12)
    await _fill(site, "andere-team", 2)
    await site.login_as(SUPER)

    response = await site.client.get("/admin/reports")

    assert response.status_code == 200
    assert "Gebruik openen" in response.text
    assert ">12<" in response.text
    assert "&lt; 5" in response.text


async def test_the_superuser_reads_every_organisation(site: Site) -> None:
    await _fill(site, "andere-team", 12)
    await site.login_as(SUPER)

    response = await site.client.get(f"/admin/groups/andere-team/usage?from={DAY}&to={DAY}")

    assert response.status_code == 200
    assert ">12<" in response.text


async def test_a_backwards_range_is_refused(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get("/admin/groups/demo/usage?from=2026-01-10&to=2026-01-01")

    assert response.status_code == 400


async def test_a_date_that_is_no_date_is_refused(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get("/admin/groups/demo/usage?from=gisteren")

    assert response.status_code == 400


async def test_the_default_range_is_the_last_four_weeks(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get("/admin/groups/demo/usage")

    assert response.status_code == 200
    assert 'name="to" type="date" value="2026-01-01"' in response.text
    assert 'name="from" type="date" value="2025-12-05"' in response.text


async def test_the_page_is_in_english_when_asked(site: Site) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(f"/admin/groups/demo/usage?from={DAY}&to={DAY}&lang=en")

    assert "Bookings created per day" in response.text
    assert "Cancelled by the planner" in response.text
    assert "Boekingen gemaakt per dag" not in response.text


async def test_the_group_page_has_a_usage_tab_and_the_menu_an_entry(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = await site.client.get("/admin/groups/demo/usage")

    assert 'href="/admin/groups/demo/usage"' in page.text
    assert 'href="/admin/reports"' in page.text


async def test_the_agent_sees_no_reports_entry(site: Site) -> None:
    await site.login_as(AGENT)

    page = await site.client.get("/admin/")

    assert 'href="/admin/reports"' not in page.text


async def test_the_organisation_admin_has_the_usage_sub_tab(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = await site.client.get("/admin/organisations/demo/usage")

    assert page.status_code == 200
    assert 'href="/admin/organisations/demo/usage"' in page.text


async def test_the_page_names_no_person_and_no_booking(site: Site) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"/admin/groups/demo/usage?from={DAY}&to={DAY}")).text

    for word in ("@", "booking_id", "token"):
        assert word not in text.split("<main", 1)[1]
