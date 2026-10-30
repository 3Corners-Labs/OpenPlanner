"""The portal shell renders the navigation register through the matrix (ADR 0047)."""

import re

from app.web.navigation import BOTTOM_BAR_ENTRIES
from tests.admin.conftest import AGENT, GROUP_ADMIN, NO_ROLE, SAME_ORIGIN, SUPER, Site


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


def _panel(page: str) -> str:
    """Return the profile panel of ``page``."""
    return page.split('<div class="admin-profile__panel">', 1)[1].split("</details>", 1)[0]


def _choices(page: str, label_id: str) -> list[tuple[str, str, bool]]:
    """Return (text, href, current) of the choice group named by ``label_id`` in the panel."""
    group = _panel(page).split(f'aria-labelledby="{label_id}"', 1)[1].split("</div>", 1)[0]
    return [
        (text, re.search(r'href="([^"]+)"', attrs).group(1), 'aria-current="true"' in attrs)  # type: ignore[union-attr]
        for attrs, text in re.findall(r"<a ([^>]*)>([^<]+)</a>", group)
    ]


def _buttons(page: str, label_id: str) -> list[tuple[str, str, bool]]:
    """Return (text, value, current) of the submit buttons of the row named by ``label_id``."""
    group = _panel(page).split(f'aria-labelledby="{label_id}"', 1)[1].split("</div>", 1)[0]
    return [
        (text, re.search(r'value="([^"]+)"', attrs).group(1), 'aria-current="true"' in attrs)  # type: ignore[union-attr]
        for attrs, text in re.findall(r"<button ([^>]*)>([^<]+)</button>", group)
    ]


def _back(page: str, form_class: str = "") -> str:
    """Return the ``next`` the profile menu's forms return to."""
    form = _panel(page).split(f'<form{form_class} method="get" action="/admin/preferences">', 1)[1]
    return re.search(r'name="next" value="([^"]*)"', form).group(1)  # type: ignore[union-attr]


def _theme(page: str) -> str | None:
    """Return the theme the page's body declares, if any."""
    found = re.search(r'<body class="admin" data-theme="([a-z]+)"', page)
    return found.group(1) if found else None


async def test_the_theme_follows_the_system_until_the_admin_chooses(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/")).text

    assert _theme(page) == "system"
    assert _buttons(page, "theme-label") == [
        ("Systeem", "system", True),
        ("Licht", "light", False),
        ("Donker", "dark", False),
    ]
    assert _back(page) == "/admin/"


async def test_a_chosen_theme_redirects_back_and_stays_for_the_session(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    chosen = await site.client.get("/admin/preferences?next=/admin/audit&theme=dark")
    back = await site.client.get(chosen.headers["location"])
    later = (await site.client.get("/admin/")).text

    assert (chosen.status_code, chosen.headers["location"]) == (303, "/admin/audit")
    cookies = {c.split("=", 1)[0] for c in chosen.headers.get_list("set-cookie")}
    assert cookies <= {"op_admin"}
    assert _theme(back.text) == "dark"
    assert _theme(later) == "dark"
    assert ("Donker", "dark", True) in _buttons(later, "theme-label")


async def test_an_unknown_theme_is_ignored_and_never_reflected(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/preferences?theme=light")

    chosen = await site.client.get("/admin/preferences?theme=%22%3E%3Cx&next=/admin/")
    page = (await site.client.get("/admin/")).text

    assert chosen.status_code == 303
    assert _theme(page) == "light"
    assert "%22%3E%3Cx" not in page
    assert '"><x' not in page


async def test_the_return_target_stays_inside_the_portal(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    elsewhere = await site.client.get("/admin/preferences?theme=dark&next=https://evil.example/")
    scheme_less = await site.client.get("/admin/preferences?theme=dark&next=//evil.example/")
    itself = await site.client.get(
        "/admin/preferences?theme=dark&next=/admin/preferences%3Ftheme%3Dlight"
    )
    missing = await site.client.get("/admin/preferences?theme=dark")

    assert {r.headers["location"] for r in (elsewhere, scheme_less, itself, missing)} == {"/admin/"}


async def test_the_forms_return_to_the_page_with_its_date_filters_only(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/audit?from=2026-10-01&level=x&booking=%3Cy")).text

    assert _back(page) == "/admin/audit?from=2026-10-01"
    assert _back(page, ' class="admin-profile__alerts"') == "/admin/audit?from=2026-10-01"
    assert "%3Cy" not in _panel(page)


async def test_the_theme_survives_a_fresh_login_but_not_a_logout(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/preferences?theme=dark")

    await site.login_as(GROUP_ADMIN)
    after_login = (await site.client.get("/admin/")).text
    await site.client.post("/admin/logout", headers=SAME_ORIGIN)
    await site.login_as(GROUP_ADMIN)
    after_logout = (await site.client.get("/admin/")).text

    assert _theme(after_login) == "dark"
    assert _theme(after_logout) == "system"


async def test_the_choices_are_buttons_and_the_current_one_is_primary(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/preferences?theme=dark")

    page = (await site.client.get("/admin/")).text

    group = _panel(page).split('aria-labelledby="theme-label"', 1)[1].split("</div>", 1)[0]
    small = 'class="button button--small'
    assert f'<button type="submit" name="theme" value="dark" {small}" aria-current="true">' in group
    assert f'<button type="submit" name="theme" value="light" {small} button--secondary">' in group


async def test_the_language_is_chosen_in_the_panel_not_in_the_top_bar(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/")).text

    assert 'class="language-switcher"' not in page.split('<details class="admin-profile">')[0]
    assert _choices(page, "language-label") == [
        ("Nederlands", "?lang=nl", True),
        ("English", "?lang=en", False),
    ]
    assert 'hreflang="en" lang="en"' in _panel(page)


async def test_the_panel_head_shows_the_name_in_bold_and_the_role_under_it(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    panel = _panel((await site.client.get("/admin/?lang=en")).text)
    await site.login_as(SUPER)
    super_panel = _panel((await site.client.get("/admin/?lang=en")).text)

    head = panel.split('<div class="admin-profile__head">', 1)[1].split("</div>", 1)[0]
    assert f'<p class="admin-profile__who">{GROUP_ADMIN.display_name}</p>' in head
    assert '<p class="admin-profile__role">Group admin</p>' in head
    assert "Signed in as" not in panel
    assert 'class="chip"' not in panel
    assert '<p class="admin-profile__role">Superuser</p>' in super_panel


async def test_the_panel_follows_the_order_of_the_design(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    panel = _panel((await site.client.get("/admin/?lang=en")).text)

    order = [
        "admin-profile__who",
        "admin-profile__role",
        "admin-profile__source",
        'href="/admin/rights"',
        'id="theme-label"',
        'id="language-label"',
        "<fieldset",
        'action="/admin/logout"',
    ]
    positions = [panel.index(part) for part in order]
    assert positions == sorted(positions)
    assert "Your role comes from your organisation account" in panel


async def test_my_rights_and_the_preferences_are_offered_only_to_who_may_use_them(
    site: Site,
) -> None:
    await site.login_as(NO_ROLE)

    page = (await site.client.get("/admin/")).text

    assert "<fieldset" not in page
    assert 'id="theme-label"' not in page
    assert 'href="/admin/rights"' not in page
    assert _choices(page, "language-label")


def _alerts(page: str) -> tuple[bool, str | None]:
    """Return whether the banner box is ticked and which pop-up option is selected."""
    form = _panel(page).split("<fieldset", 1)[1].split("</fieldset>", 1)[0]
    banner = re.search(r'<input type="checkbox"[^>]*name="alert_banner"([^>]*)>', form)
    popups = re.search(r'<option value="([a-z]+)" selected>', form)
    assert banner
    return "checked" in banner.group(1), popups.group(1) if popups else None


async def test_alert_preferences_start_with_the_banner_on_and_warnings_popping_up(
    site: Site,
) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/")).text

    assert _alerts(page) == (True, "warnings")


async def test_alert_preferences_are_kept_in_the_session(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    chosen = await site.client.get(
        "/admin/preferences?next=/admin/audit&alert_prefs=1&alert_popups=outages"
    )
    later = (await site.client.get("/admin/")).text

    assert (chosen.status_code, chosen.headers["location"]) == (303, "/admin/audit")
    cookies = {c.split("=", 1)[0] for c in chosen.headers.get_list("set-cookie")}
    assert cookies <= {"op_admin"}
    assert _alerts(later) == (False, "outages")


async def test_an_unticked_banner_box_only_counts_when_the_form_was_sent(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    await site.client.get("/admin/preferences?alert_popups=off")
    await site.client.get("/admin/preferences?alert_prefs=1&alert_banner=1&alert_popups=all")
    page = (await site.client.get("/admin/")).text

    assert _alerts(page) == (True, "all")


async def test_an_unknown_pop_up_choice_is_ignored_and_never_reflected(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/preferences?alert_prefs=1&alert_popups=all")

    await site.client.get("/admin/preferences?alert_prefs=1&alert_popups=%22%3E%3Cx")
    page = (await site.client.get("/admin/")).text

    assert _alerts(page)[1] == "all"
    assert "%22%3E%3Cx" not in page
    assert '"><x' not in page


async def test_alert_preferences_survive_a_fresh_login_but_not_a_logout(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/preferences?alert_prefs=1&alert_popups=off")

    await site.login_as(GROUP_ADMIN)
    after_login = (await site.client.get("/admin/")).text
    await site.client.post("/admin/logout", headers=SAME_ORIGIN)
    await site.login_as(GROUP_ADMIN)
    after_logout = (await site.client.get("/admin/")).text

    assert _alerts(after_login) == (False, "off")
    assert _alerts(after_logout) == (True, "warnings")
