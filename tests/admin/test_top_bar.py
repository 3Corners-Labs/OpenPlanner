"""The top bar: status, tasks and alerts before help and the profile (backlog SH-2, step 7).

The three links are shortcuts into screens that already have a home (ADR 0047), shown only to
whoever may open what they lead to. A count is read on the server; a link without a count
source shows none.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import JobRow
from tests.admin.conftest import (
    AGENT,
    AUDITOR,
    GROUP_ADMIN,
    NO_ROLE,
    ORG_ADMIN,
    SUPER,
    Site,
)
from tests.support.queue import own_queue, site_uow

LINK = re.compile(r'<a class="admin-topbar__link[^"]*" href="([^"]+)"([^>]*)>(.*?)</a>', re.DOTALL)


@dataclass(frozen=True)
class TopLink:
    """One link of the top bar as the page renders it."""

    href: str
    label: str
    count: str | None
    aria_label: str | None


def _top_bar(page: str) -> list[TopLink]:
    """Return the links of the top bar's end, in order, before the profile menu."""
    end = page.split('<div class="admin-topbar__end">', 1)[1].split(
        '<details class="admin-profile">'
    )[0]
    links = []
    for href, attrs, inner in LINK.findall(end):
        label = re.search(r'admin-topbar__label">([^<]+)<', inner)
        count = re.search(r'<span class="chip chip--accent">([^<]+)</span>', inner)
        aria = re.search(r'aria-label="([^"]+)"', attrs)
        links.append(
            TopLink(
                href,
                label.group(1) if label else "",
                count.group(1) if count else None,
                aria.group(1) if aria else None,
            )
        )
    return links


async def _seed_jobs(site: Site) -> int:
    """Make some jobs run and fail; return how many are active now."""
    await own_queue(site)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session, session.begin():
        await session.execute(
            update(JobRow)
            .where(JobRow.type == "create_video_room")
            .values(status="running", claimed_at=site.clock.now(), attempts=1)
        )
        await session.execute(
            update(JobRow).where(JobRow.type == "send_confirmation").values(status="failed")
        )
    async with site_uow(site).locked() as tx:
        return (await tx.active_jobs(1)).total


async def test_the_links_come_before_help_in_a_fixed_order(site: Site) -> None:
    await site.login_as(SUPER)

    links = _top_bar((await site.client.get("/admin/?lang=en")).text)

    assert [(link.href, link.label) for link in links] == [
        ("/admin/settings", "Status"),
        ("/admin/#widget-tasks", "Tasks"),
        ("/admin/audit?level=warning", "Alerts"),
        ("/admin/help/overview", "Help with this page"),
    ]


async def test_each_link_shows_only_to_who_may_open_what_it_leads_to(site: Site) -> None:
    expected = {
        "superuser": (SUPER, ["Status", "Tasks", "Alerts", "Help with this page"]),
        "org admin": (ORG_ADMIN, ["Tasks", "Alerts", "Help with this page"]),
        "group admin": (
            GROUP_ADMIN,
            ["Alerts", "Help with this page"],
        ),  # no tasks widget on this overview
        "auditor": (AUDITOR, ["Status", "Alerts", "Help with this page"]),  # no tasks widget either
        "agent": (AGENT, ["Help with this page"]),
    }
    for who, (principal, labels) in expected.items():
        await site.login_as(principal)

        links = _top_bar((await site.client.get("/admin/?lang=en")).text)

        assert [link.label for link in links] == labels, who


async def test_a_page_without_rights_has_no_links_to_screens(site: Site) -> None:
    await site.login_as(NO_ROLE)

    page = (await site.client.get("/admin/?lang=en")).text

    assert "admin-topbar__link" not in page.split('<details class="admin-profile">')[0]


async def test_the_tasks_link_counts_running_and_failed_jobs_for_the_screen_reader_too(
    site: Site,
) -> None:
    active = await _seed_jobs(site)
    await site.login_as(SUPER)

    tasks = next(
        link
        for link in _top_bar((await site.client.get("/admin/?lang=en")).text)
        if link.label == "Tasks"
    )

    assert active > 0
    assert tasks.count == str(active)
    assert tasks.aria_label == f"Tasks, {active} running or failed"


async def test_the_count_speaks_dutch_too(site: Site) -> None:
    active = await _seed_jobs(site)
    await site.login_as(SUPER)

    tasks = next(
        link
        for link in _top_bar((await site.client.get("/admin/?lang=nl")).text)
        if link.label == "Taken"
    )

    assert tasks.aria_label == f"Taken, {active} lopend of mislukt"


async def test_a_link_without_a_count_has_no_count_and_no_aria_label(site: Site) -> None:
    await _seed_jobs(site)
    await site.login_as(SUPER)

    links = _top_bar((await site.client.get("/admin/?lang=en")).text)

    for link in links:
        if link.label != "Tasks":
            assert link.count is None, link.label
            assert link.aria_label is None, link.label


async def test_no_active_jobs_means_no_count(site: Site) -> None:
    await site.login_as(SUPER)

    tasks = next(
        link
        for link in _top_bar((await site.client.get("/admin/?lang=en")).text)
        if link.label == "Tasks"
    )

    assert tasks.count is None
    assert tasks.aria_label is None


async def test_the_links_are_on_every_admin_page_and_the_count_is_a_number_only(site: Site) -> None:
    await _seed_jobs(site)
    await site.login_as(SUPER)

    for path in ("/admin/", "/admin/audit", "/admin/rights", "/admin/settings"):
        page = (await site.client.get(path)).text
        links = _top_bar(page)

        assert links[0].label == "Status", path
        counts = [link.count for link in links if link.count is not None]
        assert all(count.isdigit() for count in counts), path


async def test_the_overview_has_the_anchor_the_tasks_link_leads_to(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/")).text

    assert 'id="widget-tasks"' in page


TEMPLATE_AND_CODE = [*Path("app").rglob("*.html"), *Path("app/admin").rglob("*.py")]
ROLE_SWITCHER = re.compile(
    r"role[-_ ]?switch|switch[-_ ]?role|view[-_ ]as[-_ ]role|impersonat", re.I
)


def test_no_template_or_route_offers_a_role_switcher() -> None:
    offenders = [str(p) for p in TEMPLATE_AND_CODE if ROLE_SWITCHER.search(p.read_text())]

    assert offenders == [], "the role comes from the identity provider and nowhere else (ADR 0013)"


def test_the_check_finds_a_role_switcher() -> None:
    assert ROLE_SWITCHER.search('<select name="role_switcher">')
    assert ROLE_SWITCHER.search("def switch_role(request):")
