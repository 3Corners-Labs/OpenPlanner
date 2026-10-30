"""The portal shell renders the navigation register through the matrix (ADR 0047)."""

import re

from app.web.navigation import BOTTOM_BAR_ENTRIES
from tests.admin.conftest import AGENT, GROUP_ADMIN, SAME_ORIGIN, SUPER, Site


def _current_links(page: str) -> list[str]:
    """Return the hrefs of the links marked as the current page, once each.

    The side navigation and the phone bottom bar both mark the current entry (SH-1).
    """
    return list(dict.fromkeys(re.findall(r'<a href="([^"]+)" aria-current="page"', page)))


def _hrefs(markup: str) -> list[str]:
    """Return the hrefs of every link in ``markup``, in order."""
    return re.findall(r'<a href="([^"]+)"', markup)


def _side_nav(page: str) -> str:
    """Return the side navigation (desktop and tablet) of ``page``."""
    return page.split('class="admin-nav">', 1)[1].split("</nav>", 1)[0]


def _bottom_bar(page: str) -> str:
    """Return the bottom bar (phone) of ``page``."""
    return page.split('class="admin-bottombar">', 1)[1].split("</nav>", 1)[0]


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


async def test_the_bottom_bar_shows_the_first_four_entries_and_the_rest_behind_more(
    site: Site,
) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/audit?lang=en")).text
    entries = _hrefs(_side_nav(page))
    bar, more = _bottom_bar(page).split('<details class="admin-bottombar__more">', 1)

    assert len(entries) > BOTTOM_BAR_ENTRIES
    assert _hrefs(bar) == entries[:BOTTOM_BAR_ENTRIES]
    assert _hrefs(more) == entries[BOTTOM_BAR_ENTRIES:]
    assert "<summary>" in more
    assert ">More</span></summary>" in more
    assert '<span class="admin-nav__heading" id="more-manage">Manage</span>' in more
    assert '<ul aria-labelledby="more-manage">' in more
    assert 'id="nav-manage"' not in more
    assert '<a href="/admin/audit" aria-current="page">' in bar


async def test_a_short_menu_fits_the_bottom_bar_without_more(site: Site) -> None:
    await site.login_as(AGENT)

    page = (await site.client.get("/admin/")).text
    entries = _hrefs(_side_nav(page))

    assert 0 < len(entries) <= BOTTOM_BAR_ENTRIES
    assert _hrefs(_bottom_bar(page)) == entries
    assert "admin-bottombar__more" not in page
    assert "Meer" not in _bottom_bar(page)


async def test_every_navigation_link_keeps_a_visible_text_label(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/")).text
    links = re.findall(
        r"<a href=\"/admin[^\"]*\"[^>]*>(.*?)</a>", _side_nav(page) + _bottom_bar(page)
    )

    assert links
    assert all(re.search(r"<span>[^<]+</span>$", link) for link in links)
    assert all("<svg" in link for link in links)


async def test_help_and_profile_sit_in_the_top_bar(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/?lang=en")).text
    top_bar = page.split('<header class="admin-topbar">', 1)[1].split("</header>", 1)[0]

    # The overview has a manual topic (ADR 0061), so its help link opens that topic.
    assert 'href="/admin/help/overview"' in top_bar
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
