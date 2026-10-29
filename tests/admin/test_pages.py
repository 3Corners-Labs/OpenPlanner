"""The portal shell renders the navigation register through the matrix (ADR 0047)."""

import re

from tests.admin.conftest import GROUP_ADMIN, SAME_ORIGIN, SUPER, Site


def _current_links(page: str) -> list[str]:
    """Return the hrefs of the links marked as the current page."""
    return re.findall(r'<a href="([^"]+)" aria-current="page"', page)


async def test_side_navigation_is_grouped_in_sections(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/")).text

    assert '<span class="admin-nav__heading" id="nav-monitor">Bewaken</span>' in page
    assert '<span class="admin-nav__heading" id="nav-manage">Beheren</span>' in page
    assert '<span class="admin-nav__heading" id="nav-platform">Platform</span>' in page
    assert '<ul aria-labelledby="nav-platform">' in page


async def test_a_section_without_visible_entries_is_left_out(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/")).text

    assert 'id="nav-monitor"' in page
    assert 'id="nav-platform"' not in page


async def test_help_and_profile_sit_in_the_top_bar(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/?lang=en")).text
    top_bar = page.split('<header class="admin-topbar">', 1)[1].split("</header>", 1)[0]

    assert 'href="/admin/help"' in top_bar
    assert '<details class="admin-profile">' in top_bar
    assert 'action="/admin/logout"' in top_bar
    assert "Group admin" in top_bar


async def test_a_sub_page_marks_its_entry_and_tab_as_current(site: Site) -> None:
    await site.login_as(SUPER)
    staffing = (await site.client.get("/admin/groups/demo/staffing")).text
    rule = re.search(r'href="(/admin/groups/demo/staffing/\d+)"', staffing)
    assert rule is not None

    page = (await site.client.get(rule.group(1))).text

    assert _current_links(page) == ["/admin/", "/admin/groups/demo/staffing"]


async def test_an_organisation_page_marks_its_own_entry(site: Site) -> None:
    await site.login_as(SUPER)

    branding = (await site.client.get("/admin/organisations/demo/branding")).text
    channels = (await site.client.get("/admin/organisations/demo/channels")).text

    assert _current_links(branding) == ["/admin/branding"]
    assert _current_links(channels) == ["/admin/integrations"]


def _theme(page: str) -> str | None:
    """Return the theme the page's body declares, if any."""
    found = re.search(r'<body class="admin" data-theme="([a-z]+)"', page)
    return found.group(1) if found else None


async def test_the_theme_follows_the_system_until_the_admin_chooses(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/")).text

    assert _theme(page) == "system"
    assert '<a href="?theme=system" aria-current="true">Systeem</a>' in page
    assert '<a href="?theme=dark">Donker</a>' in page


async def test_a_chosen_theme_stays_for_the_session_without_a_new_cookie(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    chosen = await site.client.get("/admin/audit?theme=dark")
    later = (await site.client.get("/admin/")).text

    cookies = {c.split("=", 1)[0] for c in chosen.headers.get_list("set-cookie")}
    assert cookies <= {"op_admin"}
    assert _theme(chosen.text) == "dark"
    assert _theme(later) == "dark"
    assert '<a href="?theme=dark" aria-current="true">Donker</a>' in later


async def test_an_unknown_theme_is_ignored_and_never_reflected(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/?theme=light")

    page = (await site.client.get("/admin/?theme=%22%3E%3Cx")).text

    assert _theme(page) == "light"
    assert "%22%3E%3Cx" not in page
    assert '"><x' not in page


async def test_the_theme_survives_a_fresh_login_but_not_a_logout(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/?theme=dark")

    await site.login_as(GROUP_ADMIN)
    after_login = (await site.client.get("/admin/")).text
    await site.client.post("/admin/logout", headers=SAME_ORIGIN)
    await site.login_as(GROUP_ADMIN)
    after_logout = (await site.client.get("/admin/")).text

    assert _theme(after_login) == "dark"
    assert _theme(after_logout) == "system"
