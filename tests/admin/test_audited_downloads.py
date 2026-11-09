"""The audited downloads refuse a request that another site makes (ASVS V3.5.3, ADR 0053).

Some admin downloads write an audit event when they are opened: the audit log export
(``audit.export``, ADR 0040), the identity provider mapping (``idp_mapping.exported``, ADR 0049
point 17) and the usage report exports of an organisation and a group (``reports.export``,
ADR 0117 step 8). The admin cookie is ``SameSite=Lax``, so it travels on a top-level navigation
from any site: without a guard, a link on another site makes a signed-in admin's browser
download the file and puts an export in their name in the audit log. Each depends on
``SameOrigin``, like every form post of the portal. ``AUDITED_DOWNLOADS`` lists them, and the
privacy sweep (``tests/admin/test_privacy_sweep.py``) keeps that list complete.
"""

from urllib.parse import quote

import pytest

from app.domain.audit import Outcome
from app.domain.security_log import CROSS_SITE, REQUEST_REJECTED
from app.web.protection import require_same_origin
from app.web.route_dependencies import dependency_calls
from tests.admin.conftest import SUPER, Site
from tests.support.admin_site import audit_events, site_app
from tests.support.downloads import AUDITED_DOWNLOADS, address
from tests.support.routes import all_routes


@pytest.mark.parametrize("route", AUDITED_DOWNLOADS)
@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site"])
async def test_a_download_another_site_asks_for_is_refused_and_not_audited(
    site: Site, route: str, fetch_site: str
) -> None:
    await site.login_as(SUPER)

    response = await site.client.get(address(route), headers={"sec-fetch-site": fetch_site})

    assert response.status_code == 403
    assert "content-disposition" not in response.headers
    assert 'href="?' not in response.text  # a query-only link would ask for the download
    assert 'href="/admin/?lang=en"' in response.text  # the switcher leads to the overview
    events = [(action, outcome, reason) for _, action, outcome, reason in await audit_events(site)]
    assert (REQUEST_REJECTED, Outcome.DENIED, CROSS_SITE) in events
    assert AUDITED_DOWNLOADS[route] not in {action for action, _, _ in events}


@pytest.mark.parametrize("route", AUDITED_DOWNLOADS)
@pytest.mark.parametrize("fetch_site", ["same-origin", "none"])
async def test_a_download_from_the_portal_or_the_address_bar_goes_ahead_and_is_audited(
    site: Site, route: str, fetch_site: str
) -> None:
    # ``same-origin``: the link on the page. ``none``: typed, bookmarked or opened from another
    # program; no page can set it, so the admin opened the address themselves.
    await site.login_as(SUPER)

    response = await site.client.get(address(route), headers={"sec-fetch-site": fetch_site})

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment; ")
    events = [(actor, action, outcome) for actor, action, outcome, _ in await audit_events(site)]
    assert (SUPER.subject, AUDITED_DOWNLOADS[route], Outcome.SUCCESS) in events


@pytest.mark.parametrize("route", AUDITED_DOWNLOADS)
@pytest.mark.parametrize(
    "ahead",
    [
        {"sec-fetch-site": "none", "sec-purpose": "prefetch;prerender"},
        {"sec-fetch-site": "same-origin", "sec-purpose": "prefetch"},
        {"purpose": "prefetch"},
    ],
)
async def test_a_download_loaded_in_advance_is_refused_without_an_event(
    site: Site, route: str, ahead: dict[str, str]
) -> None:
    # A prerender on a hover over a bookmark, or of an address the address bar predicts, would
    # export in the admin's name without a click. The click itself asks again, without the
    # header, and downloads. Nobody attacks here, so no security event either.
    await site.login_as(SUPER)

    response = await site.client.get(address(route), headers=ahead)

    assert response.status_code == 403
    assert "content-disposition" not in response.headers
    actions = {action for _, action, _, _ in await audit_events(site)}
    assert AUDITED_DOWNLOADS[route] not in actions
    assert REQUEST_REJECTED not in actions


@pytest.mark.parametrize(
    ("download", "page"),
    [
        ("/admin/audit/export.csv?level=warning", "/admin/audit?level=warning"),
        ("/admin/idp-mapping/export.txt", "/admin/idp-mapping"),
        ("/admin/groups/demo/usage/export.csv?view=queue", "/admin/groups/demo/usage?view=queue"),
    ],
)
async def test_a_download_after_the_session_ended_returns_to_its_page_after_the_login(
    site: Site, download: str, page: str
) -> None:
    # The button on a page left open past the session. The login does not replay the download:
    # its last hop would come from the identity provider, cross-site, and be refused. The admin
    # is back on the page, with its view, and asks again.
    button = {"sec-fetch-site": "same-origin", "referer": f"http://testserver{page}"}

    response = await site.client.get(download, headers=button)

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/login?next={quote(page, safe='/')}"


async def test_a_typed_download_without_a_session_returns_to_the_overview_after_the_login(
    site: Site,
) -> None:
    # Without a page that asked for it, the login lands on the overview, never on the download.
    response = await site.client.get("/admin/audit/export.csv", headers={"sec-fetch-site": "none"})

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?next=/admin/"


async def test_a_refused_ordinary_page_keeps_its_query_only_language_links(site: Site) -> None:
    # Only a page at an action's address points its links elsewhere; an unknown staff member
    # is refused too, but switching the language there only shows the same page.
    await site.login_as(SUPER)

    response = await site.client.get("/admin/staff/999999")

    assert response.status_code == 404
    assert 'href="?lang=en"' in response.text


def test_the_only_guarded_gets_are_the_audited_downloads(site: Site) -> None:
    # A GET that depends on require_same_origin is an action: a login returns to the page that
    # asked for it, and a page at its address gets language links to the admin overview. So
    # each one must be a listed admin download, never a participant page.
    guarded = {
        route.path
        for route in all_routes(site_app(site).routes)
        if "GET" in route.methods and require_same_origin in dependency_calls(route)
    }

    assert guarded == set(AUDITED_DOWNLOADS)
