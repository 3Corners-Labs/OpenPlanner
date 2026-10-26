"""Organisation screens: the superuser lists, creates and edits organisations."""

import httpx

from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, SUPER, Site
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
    assert 'href="/admin/organisations/andere/branding"' in listing
    assert 'href="/admin/organisations/new"' in listing
    assert "Nederlands" in listing  # the default language by its own name


async def test_org_admin_does_not_see_the_menu_entry(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    overview = (await site.client.get("/admin/")).text
    assert f'href="{LIST}"' not in overview


async def test_create_shows_the_new_organisation_and_audits_it(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _create(site, name="Gemeente Noord", slug="noord", default_locale="en")

    assert response.status_code == 303
    assert response.headers["location"] == f"{LIST}/noord?done=created"
    page = (await site.client.get(response.headers["location"])).text
    assert "<h1>Gemeente Noord</h1>" in page
    assert 'value="noord" readonly' in page
    assert '<option value="en" selected>' in page
    assert 'href="/admin/organisations/noord/branding"' in page
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
