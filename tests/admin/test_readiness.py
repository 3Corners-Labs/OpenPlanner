"""The readiness checklist on the overview tabs (ADR 0047 §5, build step 5)."""

import re

from app.domain.authorization import Role
from tests.admin.conftest import (
    AGENT,
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)
from tests.support.principals import principal


def _checklist(page: str) -> str:
    """Return the checklist card of ``page``."""
    return page.split('aria-labelledby="readiness-heading"', 1)[1].split("</section>", 1)[0]


def _links(markup: str) -> list[str]:
    return re.findall(r'<a href="([^"]+)"', markup)


async def test_the_group_overview_shows_what_is_missing_with_links(site: Site) -> None:
    await site.login_as(SUPER)

    card = _checklist((await site.client.get("/admin/groups/demo?lang=en")).text)

    channels = card.split("A channel to send messages", 1)[0].rsplit("<li", 1)[1]

    assert "Ready for bookings" in card
    assert "Missing" in channels  # the demo organisation has no channel that can send
    assert '<a href="/admin/organisations/demo/channels">' in card
    assert '<a href="/admin/groups/demo/settings#check">' in card or "Not needed" in card
    assert "Default texts" in card or '<a href="/admin/groups/demo/messages">' in card


async def test_a_line_links_only_when_the_reader_may_open_the_tab(site: Site) -> None:
    await site.login_as(AGENT)

    card = _checklist((await site.client.get("/admin/groups/demo?lang=en")).text)

    assert _links(card) == []  # an agent opens none of the fixing tabs
    assert "Staffing for the coming weeks" in card


async def test_a_group_admin_is_not_offered_the_organisations_channels(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    card = _checklist((await site.client.get("/admin/groups/demo?lang=en")).text)

    assert "/admin/organisations/demo/channels" not in _links(card)
    assert "/admin/groups/demo/staffing" in _links(card)


async def test_the_organisation_overview_has_its_own_checklist(site: Site) -> None:
    await site.login_as(SUPER)

    card = _checklist((await site.client.get("/admin/organisations/demo?lang=en")).text)

    assert "Own branding" in card
    assert "Alternative contact options for a closure" in card
    assert set(_links(card)) <= {
        "/admin/organisations/demo/channels",
        "/admin/organisations/demo/branding",
        "/admin/organisations/demo/contacts",
    }


async def test_the_organisation_admin_reads_it_in_dutch(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    card = _checklist((await site.client.get("/admin/organisations/demo")).text)

    assert "Klaar om te boeken" in card


async def test_active_closures_are_a_filter_of_the_overview(site: Site) -> None:
    """ADR 0047 §4: no list of its own; the old path leads to the filter."""
    await site.login_as(SUPER)

    old = await site.client.get("/admin/closures")
    filtered = (await site.client.get("/admin/?closures=1&lang=en")).text
    everything = (await site.client.get("/admin/?lang=en")).text

    assert (old.status_code, old.headers["location"]) == (308, "/admin/?closures=1")
    assert "No group has an active emergency closure right now." in filtered
    assert 'href="/admin/groups/demo"' not in filtered.split('id="groups-heading"', 1)[1]
    assert 'href="/admin/groups/demo"' in everything


async def test_a_closed_group_shows_under_the_filter_with_a_link_to_its_closure(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    response = await site.client.post(
        "/admin/groups/demo/closure",
        data={
            "ends_at": "2026-01-03",
            "ends_at_time": "00:00",
            "notice_nl": "Door een storing zijn wij gesloten.",
            "notice_en": "We are closed because of an outage.",
        },
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 303

    filtered = (await site.client.get("/admin/?closures=1&lang=en")).text
    groups = filtered.split('id="groups-heading"', 1)[1]

    assert 'href="/admin/groups/demo/closure"' in groups
    assert "Emergency closure active" in groups

    # An agent sees the label without the link; an outsider's filter never shows demo.
    await site.login_as(AGENT)
    agent = (await site.client.get("/admin/?closures=1&lang=en")).text.split('id="groups-heading"')[
        1
    ]
    assert "Emergency closure active" in agent
    assert 'href="/admin/groups/demo/closure"' not in agent
    await site.login_as(OUTSIDER)
    outsider = (await site.client.get("/admin/?closures=1&lang=en")).text
    assert 'href="/admin/groups/demo"' not in outsider


async def test_an_organisation_permission_counts_only_on_its_own_organisation(site: Site) -> None:
    """Admin of ``andere`` and group admin in ``demo``: no link to demo's channels."""
    both = principal(
        "sub-both",
        "Bo Beide",
        frozenset({Role.ORG_ADMIN, Role.GROUP_ADMIN}),
        frozenset({"andere"}),
        frozenset({"demo"}),
    )
    await site.login_as(both)

    card = _checklist((await site.client.get("/admin/groups/demo?lang=en")).text)

    assert "/admin/organisations/demo/channels" not in _links(card)
