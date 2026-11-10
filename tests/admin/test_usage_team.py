"""The Team view of the usage page: off by default, organisation admin only, no export (point 9)."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import GroupRow, StaffRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.usage_staff import StaffCell
from tests.admin.conftest import AUDITOR, GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, SUPER, Site

RANGE = "from=2025-12-26&to=2026-01-01"
HOUR = datetime(2025, 12, 31, 8, tzinfo=UTC)
ORG_USAGE = "/admin/organisations/demo/usage"
SETTING = "/admin/organisations/demo/settings/reporting_staff_view_enabled"


def _engine(site: Site) -> Any:
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    return app.state.engine


async def _switch_on(site: Site) -> None:
    response = await site.client.post(SETTING, data={"value": "true"}, headers=SAME_ORIGIN)
    assert response.status_code == 303
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    await app.state.live_settings.refresh()  # the worker's loop does this every few seconds


async def _put(site: Site, calls: int, staff_id: int | None) -> None:
    """Write ``calls`` handled calls for ``staff_id`` in the demo group."""
    async with AsyncSession(_engine(site)) as session:
        group = (
            await session.execute(select(GroupRow).where(GroupRow.slug == "demo"))
        ).scalar_one()
    cell = StaffCell(
        HOUR, group.organisation_id, group.id, staff_id, "S1c", calls, 600 * calls, 600
    )
    async with SqlUnitOfWork(_engine(site)).locked() as tx:
        await tx.add_staff_cells([cell])


async def _staff_id(site: Site, subject: str) -> int:
    async with AsyncSession(_engine(site)) as session:
        row = (
            await session.execute(select(StaffRow).where(StaffRow.subject == subject))
        ).scalar_one()
    return row.id


async def test_the_team_view_is_off_by_default_and_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = await site.client.get(f"{ORG_USAGE}?view=team&{RANGE}")
    volume = (await site.client.get(f"{ORG_USAGE}?{RANGE}")).text

    assert page.status_code == 404
    nav = volume.split('class="report-views"', 1)[1].split("</nav>", 1)[0]
    assert "view=team" not in nav
    assert "uit" in nav  # the tab says it is off, and where to switch it on
    assert "/admin/organisations/demo/settings" in nav


async def test_switched_on_the_organisation_admin_sees_the_team_by_name(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    await _put(site, 8, await _staff_id(site, ORG_ADMIN.subject))
    await _put(site, 9, None)

    page = (await site.client.get(f"{ORG_USAGE}?view=team&{RANGE}")).text

    nav = page.split('class="report-views"', 1)[1].split("</nav>", 1)[0]
    assert nav.count("<a ") == 6
    assert nav.count('aria-current="page"') == 1
    assert ORG_ADMIN.display_name in page
    assert "Voormalige teamleden" in page
    assert '<th scope="row">' in page
    assert "Totaal" not in page.split('id="team-heading"', 1)[1]  # no total row
    assert "style=" not in page.split("<main", 1)[1]


async def test_a_small_count_in_the_team_table_is_hidden(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    await _put(site, 3, await _staff_id(site, ORG_ADMIN.subject))

    page = (await site.client.get(f"{ORG_USAGE}?view=team&{RANGE}")).text

    table = page.split('id="team-heading"', 1)[1].split("</table>", 1)[0]
    assert "&lt; 5" in table
    assert ">3<" not in table
    assert ">30<" not in table  # nor the minutes that would give the calls back


async def test_only_the_organisation_admin_may_open_it(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)

    for who in (GROUP_ADMIN, AUDITOR):
        await site.login_as(who)
        response = await site.client.get(f"{ORG_USAGE}?view=team&{RANGE}")
        assert response.status_code == 403, who.subject
    await site.login_as(SUPER)
    assert (await site.client.get(f"{ORG_USAGE}?view=team&{RANGE}")).status_code == 200


async def test_the_team_is_never_exported(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)

    response = await site.client.get(f"{ORG_USAGE}/export.csv?view=team&{RANGE}")

    assert response.status_code == 400


async def test_the_team_view_offers_no_export_of_its_screen(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)

    page = (await site.client.get(f"{ORG_USAGE}?view=team&{RANGE}")).text

    export = page.split('class="report-export"', 1)[1].split("</p>", 1)[0]
    assert "view=team" not in export
    assert export.count("<a ") == 1  # the organisation's tidy file stays on offer
    assert "view=tidy" in export


async def test_the_other_families_still_offer_the_export_of_their_screen(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(f"{ORG_USAGE}?view=queue&{RANGE}")).text

    export = page.split('class="report-export"', 1)[1].split("</p>", 1)[0]
    assert "view=queue" in export
    assert "view=tidy" in export
