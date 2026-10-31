"""Organisation screens (ADR 0047 step 3): the list, the organisation's page with tabs, the
superuser's create and edit forms, and the permanent redirects of the old per-organisation lists."""

import re

import httpx

from tests.admin.conftest import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.admin.test_admin_flow import audit_events

LIST = "/admin/organisations"


async def _create(site: Site, **data: str) -> httpx.Response:
    return await site.client.post(LIST, data=data, headers=SAME_ORIGIN)


async def test_superuser_finds_organisations_in_the_menu(site: Site) -> None:
    await site.login_as(SUPER)
    overview = (await site.client.get("/admin/")).text
    listing = (await site.client.get(LIST)).text

    assert f'href="{LIST}"' in overview
    assert 'href="/admin/organisations/demo"' in listing
    assert 'href="/admin/groups?org=andere"' in listing
    assert 'href="/admin/organisations/new"' in listing
    assert "Nederlands" in listing  # the default language by its own name


async def test_an_admin_of_one_organisation_lands_on_it_from_the_menu(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    overview = (await site.client.get("/admin/")).text

    response = await site.client.get(LIST)

    assert f'href="{LIST}"' in overview
    assert response.status_code == 303
    assert response.headers["location"] == f"{LIST}/demo"
    assert 'href="/admin/organisations/new"' not in (await site.client.get(f"{LIST}/demo")).text


def _tabs(page: str) -> list[str]:
    """Return the hrefs of the tabs and sub-tabs of an organisation page."""
    tabs = page.split('<nav class="tabs"', 1)[1].split("</main>", 1)[0]
    return re.findall(r'<a href="([^"]+)"', tabs.split("<section", 1)[0])


async def test_the_organisation_page_lists_its_groups_and_its_profile(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get(f"{LIST}/demo")).text

    assert "<h1>Demo-gemeente</h1>" in page
    assert 'href="/admin/groups/demo"' in page
    assert "/admin/groups/andere-team" not in page
    assert 'href="/admin/groups?org=demo"' in page
    assert 'href="/admin/organisations/demo/edit"' in page
    assert "<dd>demo</dd>" in page
    assert "<dd>Nederlands</dd>" in page


async def test_tabs_follow_the_rights_of_the_role(site: Site) -> None:
    await site.login_as(SUPER)
    superuser = _tabs((await site.client.get(f"{LIST}/demo")).text)
    await site.login_as(ORG_ADMIN)
    org_admin = _tabs((await site.client.get(f"{LIST}/demo")).text)
    await site.login_as(AUDITOR)
    auditor = (await site.client.get(f"{LIST}/demo")).text

    assert superuser == [
        "/admin/organisations/demo",
        "/admin/organisations/demo/branding",
        "/admin/organisations/demo/channels",
        "/admin/organisations/demo/contacts",
        "/admin/organisations/demo/holidays",
        "/admin/organisations/demo/closure",
        # The second row of the overview tab: its own page first, then the sub-tabs.
        "/admin/organisations/demo",
        "/admin/organisations/demo/settings",
        "/admin/organisations/demo/texts",
        "/admin/organisations/demo/embedding",
        "/admin/organisations/demo/login",
    ]
    assert "/admin/organisations/demo/branding" not in org_admin
    assert "/admin/organisations/demo/channels" not in org_admin
    assert "/admin/organisations/demo/settings" in org_admin
    # The auditor reads the overview only: one tab shows no tab row, and no edit link.
    assert '<nav class="tabs"' not in auditor
    assert "/admin/organisations/demo/edit" not in auditor
    assert 'href="/admin/groups/demo"' in auditor


async def test_other_roles_cannot_open_an_organisation_outside_their_reach(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    assert (await site.client.get(f"{LIST}/demo")).status_code == 403
    await site.login_as(OUTSIDER)
    assert (await site.client.get(f"{LIST}/demo")).status_code == 403
    assert (await site.client.get(f"{LIST}/andere")).status_code == 200


async def test_the_old_lists_redirect_permanently_into_the_organisation(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    own = {
        "/admin/organisation-settings": f"{LIST}/demo/settings",
        "/admin/embedding": f"{LIST}/demo/embedding",
        "/admin/closures": f"{LIST}/demo/closure",
    }
    for old, new in own.items():
        response = await site.client.get(old)
        assert (response.status_code, response.headers["location"]) == (308, new), old
    assert (await site.client.get("/admin/branding")).status_code == 403

    await site.login_as(SUPER)
    for old in (*own, "/admin/branding"):
        response = await site.client.get(old)
        assert (response.status_code, response.headers["location"]) == (308, LIST), old


async def test_create_shows_the_new_organisation_and_audits_it(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _create(site, name="Gemeente Noord", slug="noord", default_locale="en")

    assert response.status_code == 303
    assert response.headers["location"] == f"{LIST}/noord?done=created"
    page = (await site.client.get(response.headers["location"])).text
    assert "<h1>Gemeente Noord</h1>" in page
    assert "<dd>noord</dd>" in page
    assert "<dd>English</dd>" in page
    assert 'href="/admin/organisations/noord/branding"' in page
    assert "Deze organisatie heeft nog geen groepen" in page
    form = (await site.client.get(f"{LIST}/noord/edit")).text
    assert 'value="noord" readonly' in form
    assert '<option value="en" selected>' in form
    assert ("sub-super", "organisation.manage", "success", "") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]
    # The new organisation can be chosen when creating a group.
    assert '<option value="noord"' in (await site.client.get("/admin/groups/new")).text


async def test_invalid_input_is_linked_to_its_field_and_kept(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _create(site, name="Noord", slug="Noord!", default_locale="nl")

    assert response.status_code == 422
    assert 'href="#slug"' in response.text
    assert 'aria-describedby="slug-hint slug-error"' in response.text
    assert 'value="Noord!"' in response.text


async def test_a_taken_slug_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _create(site, name="Nog een demo", slug="demo", default_locale="nl")

    assert response.status_code == 422
    assert 'href="#slug"' in response.text


async def test_an_unknown_language_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _create(site, name="Noord", slug="noord", default_locale="xx")

    assert response.status_code == 422
    assert 'href="#default_locale"' in response.text


async def test_edit_saves_name_and_language_but_never_the_slug(site: Site) -> None:
    await site.login_as(SUPER)
    page = f"{LIST}/demo"

    response = await site.client.post(
        page,
        data={"name": "Gemeente Demo", "slug": "gekaapt", "default_locale": "en"},
        headers=SAME_ORIGIN,
    )

    assert response.headers["location"] == f"{page}?done=saved"
    listing = (await site.client.get(LIST)).text
    assert ">Gemeente Demo</a>" in listing
    assert "/admin/organisations/gekaapt" not in listing
    again = await site.client.post(
        page, data={"name": "Gemeente Demo", "default_locale": "en"}, headers=SAME_ORIGIN
    )
    assert again.headers["location"] == f"{page}?done=unchanged"


async def test_edit_refusal_keeps_the_form(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        f"{LIST}/demo", data={"name": "", "default_locale": "nl"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert 'href="#name"' in response.text
    assert 'value="demo" readonly' in response.text


async def test_cross_site_posts_are_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        LIST,
        data={"name": "Noord", "slug": "noord", "default_locale": "nl"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
