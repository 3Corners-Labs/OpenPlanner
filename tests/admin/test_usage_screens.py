"""The Queue and calls, Capacity and Channels screens as an admin sees them (ADR 0117 step 6).

The cells are written directly, as the rollup of the call, slot and message facts would, so these
tests do not depend on which facts the application writes: the screens read cells.
"""

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import GroupRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import BookingKind
from app.domain.channels import ChannelKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import UsageCell
from tests.admin.conftest import AGENT, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, Site

RANGE = "from=2025-12-26&to=2026-01-01"
HOUR = datetime(2025, 12, 31, 8, tzinfo=UTC)


async def _put(site: Site, slug: str, build: Any) -> None:
    """Write the cells ``build(organisation_id, group_id)`` returns for the group ``slug``."""
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    engine = app.state.engine
    async with AsyncSession(engine) as session:
        group = (await session.execute(select(GroupRow).where(GroupRow.slug == slug))).scalar_one()
        ids = (group.organisation_id, group.id)
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.add_usage_cells(build(*ids))


def _queue_cells(count: int) -> Any:
    def build(org: int, group: int) -> list[UsageCell]:
        return [
            UsageCell(
                HOUR,
                org,
                group,
                "Q2",
                count,
                90 * count,
                90 * count,
                bucket=1,
                booking_kind=BookingKind.SCHEDULED,
                dispatch_kind=DispatchKind.CONTACT_CENTER,
            ),
            UsageCell(HOUR, org, group, "Q5", count, 600 * count, 600 * count, bucket=3),
            UsageCell(HOUR, org, group, "Q9", count, 3, 3),
            UsageCell(
                HOUR,
                org,
                group,
                "Q1",
                count,
                0,
                0,
                booking_kind=BookingKind.DIRECT,
                dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
            ),
        ]

    return build


def _get(site: Site, scope: str, view: str, extra: str = "") -> Any:
    return site.client.get(f"{scope}/usage?view={view}&{RANGE}{extra}")


async def test_every_family_opens_and_says_so_when_nothing_is_recorded(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    for view in ("volume", "queue", "capacity", "channels"):
        response = await _get(site, "/admin/groups/demo", view)

        assert response.status_code == 200, view
        if view != "volume":
            assert (
                "Nog niets vastgelegd" in response.text or "nog niets vastgelegd" in response.text
            )


async def test_the_page_links_the_families_and_marks_the_current_one(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    text = (await _get(site, "/admin/groups/demo", "queue")).text

    nav = text.split('class="report-views"', 1)[1].split("</nav>", 1)[0]
    assert nav.count("<a ") == 4
    assert nav.count('aria-current="page"') == 1
    assert "view=queue" in nav.split('aria-current="page"')[0].rsplit("<a ", 1)[1]
    assert 'name="view" value="queue"' in text


async def test_an_unknown_family_is_refused_without_an_echo(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    for view in ("team", "<script>alert(1)</script>"):
        response = await _get(site, "/admin/groups/demo", view)

        assert response.status_code == 400
        assert "alert(1)" not in response.text


async def test_the_queue_screen_shows_charts_each_followed_by_its_table(site: Site) -> None:
    await _put(site, "demo", _queue_cells(30))
    await site.login_as(GROUP_ADMIN)

    text = (await _get(site, "/admin/groups/demo", "queue")).text

    figures = re.findall(r'<figure class="rchart">(.*?)</figure>', text, re.S)
    assert len(figures) == 3
    for figure in figures:
        assert '<svg class="rchart__svg' in figure
        assert 'role="img"' in figure
        assert "aria-labelledby" in figure
        assert re.search(r"<title id=\"chart-[a-z-]+-title\">[^<]+</title>", figure)
        assert re.search(r"<desc id=\"chart-[a-z-]+-desc\">[^<]+</desc>", figure)
        assert figure.index("</svg>") < figure.index("<table")
        assert 'scope="col"' in figure
        assert 'scope="row"' in figure
    assert "style=" not in text.split("<main", 1)[1]
    assert "<script" not in text.split("<main", 1)[1]
    assert "Wachttijd tot een gesprek verbindt" in text
    assert ">30<" in text


async def test_the_queue_screen_is_in_english_when_asked(site: Site) -> None:
    await _put(site, "demo", _queue_cells(30))
    await site.login_as(GROUP_ADMIN)

    text = (await _get(site, "/admin/groups/demo", "queue", "&lang=en")).text

    assert "Wait until a call connects" in text
    assert "How long calls last" in text
    assert "Wachttijd" not in text


async def test_a_small_count_is_hidden_in_the_table_and_the_chart_and_cannot_be_read(
    site: Site,
) -> None:
    """Counts 1 to 4 give the same page: no number, no bar height, no title carries them."""
    pages = []
    for count in (1, 4):
        await _put(site, "demo", _queue_cells(count))
        await site.login_as(GROUP_ADMIN)
        pages.append((await _get(site, "/admin/groups/demo", "queue")).text)
        site = await _fresh(site)  # noqa: PLW2901  (the next count needs an empty store)

    first, second = (re.sub(r"\s+", " ", p.split("<main", 1)[1]) for p in pages)
    assert "&lt; 5" in first
    assert first == second


async def _fresh(site: Site) -> Site:
    """Remove the cells again so the next write starts from nothing."""
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    from sqlalchemy import delete

    from app.adapters.db.models import UsageHourRow

    async with AsyncSession(app.state.engine) as session:
        await session.execute(delete(UsageHourRow))
        await session.commit()
    return site


async def test_a_one_point_difference_between_two_reports_does_not_show_a_hidden_day(
    site: Site,
) -> None:
    """Two adjacent days, one hidden: the page never prints the hidden day's value."""

    def build(org: int, group: int) -> list[UsageCell]:
        return [
            UsageCell(HOUR, org, group, "Q9", 20, 4, 4),
            UsageCell(datetime(2025, 12, 30, 8, tzinfo=UTC), org, group, "Q9", 3, 2, 2),
        ]

    await _put(site, "demo", build)
    await site.login_as(GROUP_ADMIN)

    text = (await _get(site, "/admin/groups/demo", "queue")).text
    chart = text.split('id="chart-peak-title"', 1)[1].split("</figure>", 1)[0]

    assert "2025-12-30: &lt; 5" in chart
    assert "2025-12-30 – " not in chart
    assert re.findall(r'class="rchart__marker[^>]*>', chart)  # the shown day is drawn


async def test_the_capacity_screen_has_the_utilisation_line_and_the_heat_table(site: Site) -> None:
    def build(org: int, group: int) -> list[UsageCell]:
        return [
            UsageCell(HOUR, org, group, "C1", 20, 80, 80, reason_code="booked"),
            UsageCell(HOUR, org, group, "C1", 20, 100, 100, reason_code="capacity"),
            UsageCell(HOUR, org, group, "C3", 20, 20, 20),
            UsageCell(HOUR, org, group, "C2", 20, 0, 0, reason_code="full"),
            UsageCell(HOUR, org, group, "C4", 40, 0, 0),
        ]

    await _put(site, "demo", build)
    await site.login_as(GROUP_ADMIN)

    text = (await _get(site, "/admin/groups/demo", "capacity")).text

    assert "80%" in text
    assert 'class="data-table heat-table"' in text
    assert re.search(r'<td class="heat--4">40</td>', text)
    assert text.count('<figure class="rchart">') == 2


async def test_the_heat_table_is_not_offered_for_a_short_range(site: Site) -> None:
    def build(org: int, group: int) -> list[UsageCell]:
        return [UsageCell(HOUR, org, group, "C4", 40, 0, 0)]

    await _put(site, "demo", build)
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(
        "/admin/groups/demo/usage?view=capacity&from=2025-12-30&to=2026-01-01"
    )

    assert "heat-table" not in response.text
    assert "minstens 7 dagen" in response.text


async def test_the_channels_screen_shows_messages_languages_and_the_trial_funnel(
    site: Site,
) -> None:
    def build(org: int, group: int) -> list[UsageCell]:
        return [
            UsageCell(
                HOUR,
                org,
                group,
                "M1",
                60,
                0,
                0,
                channel_kind=ChannelKind.EMAIL,
                reason_code="reminder.sent",
            ),
            UsageCell(
                HOUR,
                org,
                group,
                "M1",
                9,
                0,
                0,
                channel_kind=ChannelKind.EMAIL,
                reason_code="reminder.failed",
            ),
            UsageCell(HOUR, org, group, "M2", 50, 0, 0, locale="nl"),
            UsageCell(HOUR, org, group, "M2", 20, 0, 0, locale="en"),
            UsageCell(HOUR, org, group, "T1", 40, 0, 0, reason_code="created"),
            UsageCell(HOUR, org, group, "T1", 20, 0, 0, reason_code="joined"),
        ]

    await _put(site, "demo", build)
    await site.login_as(GROUP_ADMIN)

    text = (await _get(site, "/admin/groups/demo", "channels", "&lang=en")).text

    assert "Messages sent and failed" in text
    assert "Reminder" in text
    assert ">50<" in text
    assert ">20<" in text
    assert "50%" in text  # followed the link, of those made
    assert text.count('<figure class="rchart">') == 2


async def test_the_organisation_page_adds_up_its_groups_for_every_family(site: Site) -> None:
    await _put(site, "demo", _queue_cells(30))
    await site.login_as(ORG_ADMIN)

    for view in ("queue", "capacity", "channels"):
        response = await _get(site, "/admin/organisations/demo", view)

        assert response.status_code == 200, view
    assert ">30<" in (await _get(site, "/admin/organisations/demo", "queue")).text


async def test_another_organisation_and_the_agent_see_no_family(site: Site) -> None:
    await _put(site, "demo", _queue_cells(30))

    await site.login_as(OUTSIDER)
    for view in ("queue", "capacity", "channels"):
        group = await _get(site, "/admin/groups/demo", view)
        org = await _get(site, "/admin/organisations/demo", view)
        assert group.status_code == org.status_code == 403
        assert ">30<" not in group.text + org.text

    await site.login_as(AGENT)
    assert (await _get(site, "/admin/groups/demo", "queue")).status_code == 403


async def test_the_superuser_reads_another_organisations_group(site: Site) -> None:
    await _put(site, "andere-team", _queue_cells(30))
    await site.login_as(SUPER)

    response = await _get(site, "/admin/groups/andere-team", "queue")

    assert response.status_code == 200
    assert ">30<" in response.text


async def test_the_page_names_no_person_and_no_booking(site: Site) -> None:
    await _put(site, "demo", _queue_cells(30))
    await site.login_as(GROUP_ADMIN)

    for view in ("queue", "capacity", "channels"):
        text = (await _get(site, "/admin/groups/demo", view)).text.split("<main", 1)[1]
        for word in ("@", "booking_id", "token", "guest_join_url"):
            assert word not in text


def _volume_cells(count: int) -> Any:
    def build(org: int, group: int) -> list[UsageCell]:
        from app.domain.usage import UsageOutcome  # noqa: PLC0415

        return [
            UsageCell(HOUR, org, group, "U1", count, 0, 0),
            UsageCell(HOUR, org, group, "U2", count, 0, 0, outcome=UsageOutcome.COMPLETED),
            UsageCell(HOUR, org, group, "U2", count // 2, 0, 0, outcome=UsageOutcome.NO_SHOW),
            UsageCell(
                HOUR,
                org,
                group,
                "Q1",
                count,
                0,
                0,
                booking_kind=BookingKind.SCHEDULED,
                dispatch_kind=DispatchKind.CONTACT_CENTER,
            ),
            UsageCell(HOUR, org, group, "M2", count, 0, 0, locale="nl"),
        ]

    return build


async def test_the_volume_screen_has_tiles_two_charts_and_the_range_form_split(site: Site) -> None:
    await _put(site, "demo", _volume_cells(40))
    await site.login_as(GROUP_ADMIN)

    text = (await _get(site, "/admin/groups/demo", "volume")).text

    main = text.split("<main", 1)[1]
    assert main.count('class="tile"') == 4
    assert "No-showpercentage" in main
    assert len(re.findall(r'<figure class="rchart">', main)) == 2
    for figure in re.findall(r'<figure class="rchart">(.*?)</figure>', main, re.S):
        assert figure.index("</svg>") < figure.index("<table")
    assert 'name="split"' in main
    assert "style=" not in main
    assert "<script" not in main


async def test_a_split_shows_its_table_and_an_unknown_one_is_refused(site: Site) -> None:
    await _put(site, "demo", _volume_cells(40))
    await site.login_as(GROUP_ADMIN)

    queue = await site.client.get(f"/admin/groups/demo/usage?view=volume&{RANGE}&split=queue")
    bad = await site.client.get(f"/admin/groups/demo/usage?view=volume&{RANGE}&split=<b>x</b>")

    assert queue.status_code == 200
    assert 'id="split-heading"' in queue.text
    assert bad.status_code == 400
    assert "<b>x</b>" not in bad.text


async def test_a_table_with_a_hidden_count_says_why(site: Site) -> None:
    await _put(site, "demo", _volume_cells(3))
    await site.login_as(GROUP_ADMIN)

    text = (await _get(site, "/admin/groups/demo", "volume")).text

    assert "zijn verborgen" in text
    assert "&lt; 5" in text


async def test_the_capacity_screen_has_its_four_tiles_with_what_they_are_made_of(
    site: Site,
) -> None:
    def build(org: int, group: int) -> list[UsageCell]:
        return [
            UsageCell(HOUR, org, group, "C1", 30, 120, 4, reason_code="capacity"),
            UsageCell(HOUR, org, group, "C1", 30, 90, 3, reason_code="booked"),
            UsageCell(HOUR, org, group, "C3", 30, 30, 1),
            UsageCell(HOUR, org, group, "C2", 12, 0, 0, reason_code="full"),
            UsageCell(HOUR, org, group, "C2", 18, 0, 0, reason_code="under_full"),
            UsageCell(HOUR, org, group, "C5", 9, 0, 0, reason_code="accepted"),
        ]

    await _put(site, "demo", build)
    await site.login_as(GROUP_ADMIN)

    text = (await _get(site, "/admin/groups/demo", "capacity")).text

    assert text.count('class="tile"') == 4
    assert "90 van 120 plaatsen" in text
    assert "12 van 30" in text
