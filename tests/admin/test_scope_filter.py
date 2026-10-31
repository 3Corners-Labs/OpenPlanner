"""The scope filter in the top bar: organisation › group, a GET form (backlog SH-3, ADR 0061 §9).

It narrows what the overview shows to what the admin already may see. It never widens, never
edits, and a value outside the admin's scope is ignored and not echoed.
"""

import re

from app.domain.audit import AuditEvent, Outcome
from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, Site

DEMO = ("Demo-gemeente", "demo", "Demo: videogesprek")
OTHER = ("Andere gemeente", "andere-team", "Andere: team")
PANEL = re.compile(r'<details class="admin-scope[^"]*">(.*?)</details>', re.DOTALL)
CARD = re.compile(r'<li class="card group-card">\s*<h3><a href="/admin/groups/([^"]+)">')


def _panel(page: str) -> str:
    found = PANEL.search(page)
    assert found, "no scope filter on this page"
    return found.group(1)


def _cards(page: str) -> list[str]:
    return CARD.findall(page)


def _options(panel: str, name: str) -> list[tuple[str, bool]]:
    select = re.search(rf'<select id="scope-{name}" name="{name}">(.*?)</select>', panel, re.DOTALL)
    assert select
    return [
        (value, "selected" in rest)
        for value, rest in re.findall(r'<option value="([^"]*)"([^>]*)>', select.group(1))
    ]


async def test_the_overview_has_the_filter_as_a_get_form(site: Site) -> None:
    await site.login_as(SUPER)

    panel = _panel((await site.client.get("/admin/?lang=en")).text)

    assert '<form method="get"' in panel
    assert 'method="post"' not in panel
    assert 'name="scope" value="1"' in panel
    assert _options(panel, "org") == [("", True), ("andere", False), ("demo", False)]
    assert _options(panel, "group") == [("", True), ("andere-team", False), ("demo", False)]


async def test_only_the_pages_that_honour_the_filter_show_it(site: Site) -> None:
    await site.login_as(SUPER)

    for path in ("/admin/audit", "/admin/rights", "/admin/groups/demo", "/admin/settings"):
        assert 'class="admin-scope"' not in (await site.client.get(path)).text, path


async def test_the_options_are_only_what_the_admin_may_see(site: Site) -> None:
    for principal, groups in (
        (ORG_ADMIN, ["demo"]),
        (GROUP_ADMIN, ["demo"]),
        (OUTSIDER, ["andere-team"]),
    ):
        await site.login_as(principal)

        panel = _panel((await site.client.get("/admin/")).text)

        assert [v for v, _ in _options(panel, "group") if v] == groups
        assert "Andere" not in panel if groups == ["demo"] else "Demo" not in panel


async def test_choosing_a_group_narrows_cards_and_widgets(site: Site) -> None:
    await site.login_as(SUPER)
    both = (await site.client.get("/admin/")).text
    assert sorted(_cards(both)) == ["andere-team", "demo"]

    page = (await site.client.get("/admin/?scope=1&group=andere-team")).text

    assert _cards(page) == ["andere-team"]
    today = page.split('data-widget="bookings-today"', 1)[1].split("</section>", 1)[0]
    assert "Andere: team" in today
    assert "Demo: videogesprek" not in today
    assert _options(_panel(page), "group") == [("", False), ("andere-team", True), ("demo", False)]
    assert _options(_panel(page), "org") == [("", False), ("andere", True), ("demo", False)]


async def test_choosing_an_organisation_keeps_all_its_groups(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/?scope=1&org=demo")).text

    assert _cards(page) == ["demo"]
    assert _options(_panel(page), "org")[2] == ("demo", True)
    assert _options(_panel(page), "group")[0] == ("", True)


async def test_the_group_wins_when_it_does_not_belong_to_the_organisation(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/?scope=1&org=demo&group=andere-team")).text

    assert _cards(page) == ["andere-team"]
    assert _options(_panel(page), "org")[1] == ("andere", True)


async def test_the_choice_stays_for_the_session_and_all_clears_it(site: Site) -> None:
    await site.login_as(SUPER)

    chosen = await site.client.get("/admin/?scope=1&group=demo")
    later = (await site.client.get("/admin/")).text
    cleared = (await site.client.get("/admin/?scope=1&org=&group=")).text

    cookies = {c.split("=", 1)[0] for c in chosen.headers.get_list("set-cookie")}
    assert cookies <= {"op_admin"}
    assert _cards(later) == ["demo"]
    assert sorted(_cards(cleared)) == ["andere-team", "demo"]


async def test_no_form_field_no_change(site: Site) -> None:
    await site.login_as(SUPER)
    await site.client.get("/admin/?scope=1&group=demo")

    page = (await site.client.get("/admin/?group=andere-team&org=andere")).text

    assert _cards(page) == ["demo"]


async def test_a_choice_ends_with_the_session(site: Site) -> None:
    await site.login_as(SUPER)
    await site.client.get("/admin/?scope=1&group=demo")

    await site.login_as(SUPER)

    assert sorted(_cards((await site.client.get("/admin/")).text)) == ["andere-team", "demo"]


async def test_a_value_outside_the_scope_is_ignored_and_never_echoed(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    for query in (
        "scope=1&group=andere-team",
        "scope=1&org=andere",
        "scope=1&org=andere&group=andere-team",
        "scope=1&group=%22%3E%3Cscript%3E",
        "scope=1&org=" + "x" * 5000,
    ):
        page = (await site.client.get(f"/admin/?{query}")).text

        assert _cards(page) == ["demo"], query
        panel = _panel(page)
        assert [v for v, selected in _options(panel, "group") if selected] == [""]
        assert [v for v, selected in _options(panel, "org") if selected] == [""]
        assert "andere-team" not in page, query
        assert "Andere" not in page, query
        assert "<script>" not in page, query
        assert "xxxx" not in page, query


async def test_a_value_outside_the_scope_is_not_kept_in_the_session(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/?scope=1&group=andere-team")

    page = (await site.client.get("/admin/")).text

    assert _cards(page) == ["demo"]
    assert [v for v, selected in _options(_panel(page), "group") if selected] == [""]


async def test_the_widget_fragments_follow_the_same_choice(site: Site) -> None:
    await site.login_as(SUPER)
    await site.client.get("/admin/?scope=1&group=andere-team")

    text = (await site.client.get("/admin/overview/w/bookings-today")).text

    assert "Andere: team" in text
    assert "Demo: videogesprek" not in text


async def test_the_changes_widget_keeps_only_the_chosen_scope(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    now = site.clock.now()
    async with app.state.admin_service._uow.locked() as tx:  # noqa: SLF001
        await tx.add_audit_event(
            AuditEvent(now, "sub-super", "group.edit", Outcome.SUCCESS, "", "demo", "demo", "name")
        )
        await tx.add_audit_event(
            AuditEvent(
                now, "sub-super", "group.edit", Outcome.SUCCESS, "", "andere", "andere-team", "x"
            )
        )
    await site.login_as(SUPER)
    everything = (await site.client.get("/admin/overview/w/changes")).text
    assert everything.count('class="widget-list__item"') == 2

    await site.client.get("/admin/?scope=1&group=andere-team")
    narrowed = (await site.client.get("/admin/overview/w/changes")).text
    await site.client.get("/admin/?scope=1&org=demo")
    by_org = (await site.client.get("/admin/overview/w/changes")).text

    assert narrowed.count('class="widget-list__item"') == 1
    assert "andere / andere-team" in narrowed
    assert by_org.count('class="widget-list__item"') == 1
    assert "demo / demo" in by_org


async def test_the_summary_names_the_scope_and_links_to_its_pages(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/?lang=en&scope=1&group=demo")).text

    summary = page.split('<details class="admin-scope admin-scope--active">', 1)[1].split(
        "</summary>", 1
    )[0]
    assert "Demo-gemeente › Demo: videogesprek" in summary
    panel = _panel(page)
    assert '<a href="/admin/groups/demo">Demo: videogesprek</a>' in panel
    assert '<a href="/admin/organisations/demo">Demo-gemeente</a>' in panel


async def test_who_may_open_the_organisation_page_gets_a_link_to_it(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    org_admin = _panel((await site.client.get("/admin/?lang=en&scope=1&org=demo")).text)
    await site.login_as(GROUP_ADMIN)
    group_admin = _panel((await site.client.get("/admin/?lang=en&scope=1&group=demo")).text)

    # ADR 0047 step 3: organisation.view opens the page, so the filter links to it.
    assert '<a href="/admin/organisations/demo">Demo-gemeente</a>' in org_admin
    assert "Demo-gemeente" in group_admin
    assert 'href="/admin/organisations/demo"' not in group_admin


async def test_a_narrowed_view_is_marked_and_everything_is_not(site: Site) -> None:
    await site.login_as(SUPER)

    everything = (await site.client.get("/admin/")).text
    narrowed = (await site.client.get("/admin/?scope=1&group=demo")).text

    assert '<details class="admin-scope">' in everything
    assert "admin-scope--active" not in everything
    assert '<details class="admin-scope admin-scope--active">' in narrowed
