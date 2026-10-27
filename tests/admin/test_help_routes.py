"""The manual in the portal (ADR 0033): help pages, the page link and the (i) disclosures."""

import re

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.admin.routes import PUBLIC_ADMIN_PATHS
from app.config import Settings
from app.main import create_app
from app.web.help import topic_for_path
from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SUPER, Site
from tests.support.routes import all_routes

SETTINGS = "/admin/groups/demo/settings"

NOT_YET_DOCUMENTED: frozenset[str] = frozenset(
    {
        "/admin/",
        "/admin/api",
        "/admin/api/clients/new",
        "/admin/api/clients/{client_id}",
        "/admin/audit",
        "/admin/branding",
        "/admin/closures",
        "/admin/groups/new",
        "/admin/groups/{slug}",
        "/admin/groups/{slug}/agenda",
        "/admin/groups/{slug}/bookings/{booking_id}",
        "/admin/groups/{slug}/closure",
        "/admin/groups/{slug}/closure/bookings/{booking_id}/move",
        "/admin/groups/{slug}/exceptions",
        "/admin/groups/{slug}/holidays",
        "/admin/groups/{slug}/messages",
        "/admin/groups/{slug}/messages/{text_locale}",
        "/admin/groups/{slug}/reminders",
        "/admin/groups/{slug}/staffing",
        "/admin/groups/{slug}/staffing/{rule_id}",
        "/admin/groups/{slug}/staffing/{rule_id}/split",
        "/admin/holidays",
        "/admin/holidays/{calendar}",
        "/admin/integrations",
        "/admin/organisations",
        "/admin/organisations/new",
        "/admin/organisations/{org}",
        "/admin/organisations/{org}/branding",
        "/admin/organisations/{org}/channels",
        "/admin/organisations/{org}/closure",
        "/admin/organisations/{org}/holidays",
        "/admin/rights",
        "/admin/updates",
    }
)
"""Admin pages without a manual topic yet. Only shrinks: document a screen, remove it here."""


def _page_routes(app: FastAPI) -> set[str]:
    """Return the admin GET routes that render a page (not the login flow, not help itself)."""
    return {
        route.path
        for route in all_routes(app.routes)
        if "GET" in route.methods
        and route.path.startswith("/admin")
        and not route.path.startswith("/admin/help")
        and route.path not in PUBLIC_ADMIN_PATHS
        and route.response_class is HTMLResponse
    }


def test_every_admin_page_has_a_topic_or_is_listed_as_not_yet_documented(
    settings: Settings,
) -> None:
    pages = _page_routes(create_app(settings))
    undocumented = {path for path in pages if topic_for_path(path) is None}

    assert undocumented - NOT_YET_DOCUMENTED == set(), "write a manual topic (ADR 0033)"
    assert NOT_YET_DOCUMENTED - undocumented == set(), "documented or gone: remove it from the list"


async def test_settings_fields_carry_their_manual_section(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(SETTINGS)).text

    assert '<details class="more" id="slot_minutes-more">' in page
    assert 'Meer uitleg<span class="visually-hidden"> bij Duur van een gesprek (minuten)' in page
    assert 'href="/admin/help/group-settings#field-slot_minutes"' in page
    # Every input of the screen has a section, so no input lacks its disclosure.
    inputs = set(re.findall(r'<(?:input|select|textarea) id="[a-z_]+" name="([a-z_]+)"', page))
    assert inputs
    assert set(re.findall(r'<details class="more" id="([a-z_]+)-more">', page)) == inputs


async def test_a_page_with_a_topic_links_to_it(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    settings_page = (await site.client.get(SETTINGS)).text
    dashboard = (await site.client.get("/admin/")).text

    assert 'href="/admin/help/group-settings"' in settings_page
    assert 'class="page-help"' not in dashboard


async def test_help_page_renders_the_manual_in_the_admin_language(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    dutch = await site.client.get("/admin/help/group-settings")
    english = await site.client.get("/admin/help/group-settings?lang=en")

    assert dutch.status_code == 200
    assert "<h1>Groepsinstellingen</h1>" in dutch.text
    assert 'id="field-slot_minutes"' in dutch.text
    assert "<h1>Group settings</h1>" in english.text
    assert "github.com" not in dutch.text


async def test_unknown_topic_is_not_found(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.get("/admin/help/nope")

    assert response.status_code == 404


async def test_topic_list_shows_only_screens_the_admin_can_open(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    group_admin = (await site.client.get("/admin/help")).text
    await site.login_as(ORG_ADMIN)
    org_admin = (await site.client.get("/admin/help")).text

    assert 'href="/admin/help/group-settings"' not in group_admin
    assert 'href="/admin/help/group-settings"' in org_admin
    assert 'href="/admin/help"' in org_admin  # in the navigation
