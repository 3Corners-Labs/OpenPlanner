"""The staff directory pages (ADR 0046): just-in-time records, exact per scope."""

from datetime import timedelta

from tests.admin.conftest import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, Site


async def _id_of(site: Site, name: str) -> str:
    await site.login_as(SUPER)
    text = (await site.client.get("/admin/staff")).text
    return text.split(f'">{name}</a>')[0].rsplit('href="/admin/staff/', 1)[1]


async def test_a_login_puts_the_admin_in_the_list(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.login_as(ORG_ADMIN)

    text = (await site.client.get("/admin/staff")).text

    assert '<h1 id="page-title">Medewerkers</h1>' in text
    assert ">Gina Group</a>" in text
    assert ">Olaf Org</a>" in text
    assert "Groepsbeheerder" in text
    assert 'href="/admin/staff"' in text  # the menu entry


async def test_each_viewer_sees_their_own_scope_and_no_superuser(site: Site) -> None:
    for principal in (SUPER, GROUP_ADMIN, ORG_ADMIN, OUTSIDER):
        await site.login_as(principal)

    seen_by_outsider = (await site.client.get("/admin/staff")).text
    await site.login_as(GROUP_ADMIN)
    seen_by_group = (await site.client.get("/admin/staff")).text
    await site.login_as(AUDITOR)
    seen_by_auditor = (await site.client.get("/admin/staff")).text

    assert ">Otto Other</a>" in seen_by_outsider
    assert "Gina Group</a>" not in seen_by_outsider
    assert ">Gina Group</a>" in seen_by_group
    assert "Olaf Org</a>" not in seen_by_group  # an organisation admin is not in the group
    for name in ("Gina Group", "Olaf Org", "Anouk Audit"):
        assert f">{name}</a>" in seen_by_auditor
    for text in (seen_by_outsider, seen_by_group, seen_by_auditor):
        assert "Sanne Super" not in text


async def test_a_person_outside_the_scope_does_not_exist(site: Site) -> None:
    await site.login_as(OUTSIDER)
    other = await _id_of(site, "Otto Other")
    await site.login_as(ORG_ADMIN)

    response = await site.client.get(f"/admin/staff/{other}")

    assert response.status_code == 404
    assert "Otto" not in response.text


async def test_the_detail_page_shows_access_times_and_activity(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.post("/admin/logout", headers={"sec-fetch-site": "same-origin"})
    gina = await _id_of(site, "Gina Group")
    await site.login_as(ORG_ADMIN)

    text = (await site.client.get(f"/admin/staff/{gina}")).text

    assert '<h1 id="page-title">Gina Group</h1>' in text
    assert "Groepsbeheerder" in text
    assert "Laatst afgemeld" in text
    assert "Eerste keer ingelogd" in text  # the staff.created event in the activity table
    assert "Uitloggen" in text  # the logout event


async def test_dormant_people_are_marked_and_can_be_filtered(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    site.clock.advance(timedelta(days=91))
    await site.login_as(ORG_ADMIN)

    everyone = (await site.client.get("/admin/staff")).text
    dormant = (await site.client.get("/admin/staff", params={"dormant": "1"})).text

    assert "Lang niet ingelogd" in everyone
    assert ">Gina Group</a>" in dormant
    assert "Olaf Org</a>" not in dormant


async def test_filters_outside_the_scope_are_ignored(site: Site) -> None:
    await site.login_as(OUTSIDER)
    await site.login_as(ORG_ADMIN)

    text = (
        await site.client.get("/admin/staff", params={"organisation": "andere", "role": "nope"})
    ).text

    assert ">Olaf Org</a>" in text
    assert "Otto Other</a>" not in text
