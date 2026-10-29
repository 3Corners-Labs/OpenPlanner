"""Embedding screen (ADR 0035 §3): allowed origins per organisation, refusals, audit, nav."""

import httpx

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, Site
from tests.admin.test_admin_flow import audit_events

PAGE = "/admin/organisations/demo/embedding"


async def _save(site: Site, origins: str) -> httpx.Response:
    return await site.client.post(PAGE, data={"origins": origins}, headers=SAME_ORIGIN)


async def test_the_menu_links_to_the_overview_with_the_own_organisation(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    dashboard = (await site.client.get("/admin/")).text
    overview = (await site.client.get("/admin/embedding")).text

    assert 'href="/admin/embedding"' in dashboard
    assert f'href="{PAGE}"' in overview
    assert "/admin/organisations/andere/embedding" not in overview


async def test_origins_are_saved_normalised_and_audited_with_counts(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await _save(
        site, "https://WWW.Gemeente.example/\r\n\r\nhttps://werk.gemeente.example"
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=saved"
    page = (await site.client.get(response.headers["location"])).text
    assert "https://www.gemeente.example\nhttps://werk.gemeente.example</textarea>" in page
    assert "2 websites toegestaan." in page
    assert (
        "sub-org",
        "embedding.manage",
        "success",
        "",  # the detail holds counts (test_embedding_service), never the origins
    ) in [(a, b, str(c), d) for a, b, c, d in await audit_events(site)]

    again = await _save(site, "https://www.gemeente.example\nhttps://werk.gemeente.example/")
    assert again.headers["location"] == f"{PAGE}?done=unchanged"
    removed = await _save(site, "")
    assert removed.headers["location"] == f"{PAGE}?done=saved"
    assert "Nog geen websites toegestaan" in (await site.client.get(PAGE)).text


async def test_a_wildcard_is_refused_with_its_line_linked_to_the_textarea(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await _save(site, "https://www.gemeente.example\nhttps://*.gemeente.example")

    assert response.status_code == 422
    assert 'href="#origins"' in response.text
    assert "Regel 2 is geen website." in response.text
    assert 'aria-describedby="origins-hint origins-error"' in response.text
    assert "https://*.gemeente.example</textarea>" in response.text  # kept for correction
    assert "Nog geen websites toegestaan" in response.text  # nothing stored


async def test_localhost_is_accepted_on_a_test_installation(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(PAGE)).text
    response = await _save(site, "http://localhost:5173")

    assert "Op deze testomgeving mag ook http://localhost" in page
    assert response.status_code == 303


async def test_the_groups_and_their_embedding_switch_are_listed(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(PAGE)).text

    assert 'href="/admin/groups/demo/settings"' in page
    assert "Uit</span>" in page


async def test_group_admins_cannot_open_the_page(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    assert (await site.client.get(PAGE)).status_code == 403
    assert (await _save(site, "https://www.gemeente.example")).status_code == 403
    assert 'href="/admin/embedding"' not in (await site.client.get("/admin/")).text


async def test_cross_site_posts_are_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        PAGE, data={"origins": "https://evil.example"}, headers={"sec-fetch-site": "cross-site"}
    )

    assert response.status_code == 403
