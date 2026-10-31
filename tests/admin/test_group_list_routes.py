"""The groups list, *Manage → Groups* (ADR 0047 §4 and step 5)."""

import re

from app.admin.group_list_routes import MAX_QUERY_LENGTH, narrow
from app.domain.scheduling import Group, GroupSummary
from tests.admin.conftest import AGENT, GROUP_ADMIN, ORG_ADMIN, SUPER, Site

LIST = "/admin/groups"


def _rows(page: str) -> list[str]:
    """Return the hrefs of the group links in the table, in order."""
    table = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0] if "<tbody>" in page else ""
    return re.findall(r'<th scope="row"><a href="([^"]+)"', table)


async def test_the_menu_entry_opens_the_list_with_every_group_in_scope(site: Site) -> None:
    await site.login_as(SUPER)
    overview = (await site.client.get("/admin/")).text

    page = (await site.client.get(LIST)).text

    assert f'<a href="{LIST}"' in overview
    assert _rows(page) == ["/admin/groups/andere-team", "/admin/groups/demo"]
    assert "Aantal groepen: 2" in page
    assert 'href="/admin/organisations/demo"' in page
    assert 'href="/admin/groups/new"' in page
    assert 'href="/g/demo"' in page
    assert '<form class="card" method="get" action="/admin/groups"' in page
    assert 'method="post"' not in page.split("<main", 1)[1]  # a list, never an edit form


async def test_the_list_shows_only_the_groups_the_role_may_view(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    group_admin = (await site.client.get(LIST)).text
    await site.login_as(AGENT)
    agent = (await site.client.get(LIST)).text
    await site.login_as(ORG_ADMIN)
    org_admin = (await site.client.get(LIST)).text

    assert _rows(group_admin) == ["/admin/groups/demo"]
    assert _rows(agent) == ["/admin/groups/demo"]
    assert _rows(org_admin) == ["/admin/groups/demo"]
    # Only who may open the organisation gets a link to it; a group admin sees the name.
    assert "/admin/organisations/demo" not in group_admin
    assert "Demo-gemeente" in group_admin
    assert 'href="/admin/organisations/demo"' in org_admin
    assert 'href="/admin/groups/new"' not in group_admin
    # One organisation in reach: no organisation filter to choose from.
    assert 'name="org"' not in org_admin


async def test_search_and_organisation_filter_narrow_the_list(site: Site) -> None:
    await site.login_as(SUPER)

    by_name = (await site.client.get(LIST, params={"q": "VIDEO"})).text
    by_org = (await site.client.get(LIST, params={"org": "andere"})).text
    nothing = (await site.client.get(LIST, params={"q": "bestaat niet"})).text

    assert _rows(by_name) == ["/admin/groups/demo"]
    assert "Aantal groepen: 1" in by_name
    assert "Van 2 groepen in totaal." in by_name
    # The typed text is never shown again (ADR 0063 §3); the page only says a search is active.
    assert "VIDEO" not in by_name.split("<main", 1)[1]
    assert "A search is active" not in by_name  # the Dutch page says it in Dutch
    assert "Er is een zoekopdracht actief" in by_name
    assert _rows(by_org) == ["/admin/groups/andere-team"]
    assert '<option value="andere" selected>' in by_org
    assert 'href="/admin/groups"' in by_org  # clear filters
    assert _rows(nothing) == []
    assert "Geen groepen gevonden." in nothing


async def test_a_filter_value_outside_the_scope_is_ignored_and_never_echoed(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(LIST, params={"org": "andere"})).text
    injected = (await site.client.get(LIST, params={"org": '"><script>x</script>'})).text

    assert _rows(page) == ["/admin/groups/demo"]
    assert "andere" not in page.split("<main", 1)[1]
    assert "<script>" not in injected
    assert _rows(injected) == ["/admin/groups/demo"]


async def test_a_long_search_text_is_cut_and_escaped(site: Site) -> None:
    await site.login_as(SUPER)
    text = "<b>" + "a" * 200

    page = (await site.client.get(LIST, params={"q": text})).text

    assert "<b>" not in page.split("<main", 1)[1]
    assert "a" * (MAX_QUERY_LENGTH + 1) not in page


def _summary(slug: str, name: str, org: str) -> GroupSummary:
    return GroupSummary(Group(1, 1, name, slug, "Europe/Amsterdam", 30), org, org.title())


def test_narrow_matches_name_or_slug_and_drops_an_unknown_organisation() -> None:
    groups = [_summary("demo", "Demo: videogesprek", "demo"), _summary("team", "Team", "andere")]

    rows, filters = narrow(groups, "  Video ", "nope")
    by_slug, _ = narrow(groups, "tea", "")
    by_org, chosen = narrow(groups, "", "andere")

    assert [g.group.slug for g in rows] == ["demo"]
    assert (filters.query, filters.organisation, filters.active) == ("Video", "", True)
    assert [o.value for o in filters.organisations] == ["demo", "andere"]
    assert [g.group.slug for g in by_slug] == ["team"]
    assert [g.group.slug for g in by_org] == ["team"]
    assert chosen.organisation == "andere"
