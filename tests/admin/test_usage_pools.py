"""The pools of an organisation on its *Capacity* screen (ADR 0122 step 5; G41).

A counted pool of the demo organisation, two units on Mondays 09:00 to 12:00 Amsterdam, with six
appointments on Monday 5 January 2026. The clock moves past the closing delay, so those hours
count. Only a reader of the whole organisation sees the pool; the group's own page has none.
"""

from datetime import time, timedelta

from app.domain.authorization import Scope
from app.domain.booking import BookingKind, Slot
from app.domain.scheduling import Offer, ScheduleRule, TimeWindow
from app.domain.slot_close import CLOSE_DELAY
from tests.support.admin_site import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, Site, site_uow
from tests.support.closures import confirmed_booking
from tests.support.domain import SLOT_0900
from tests.support.skill_seed import add_pool

EVERYTHING = Scope(everything=True)
RANGE = "from=2026-01-05&to=2026-01-05"
PAGE = f"/admin/organisations/demo/usage?view=capacity&{RANGE}"


async def staffed_pool_with_appointments(site: Site, appointments: int = 6) -> None:
    pool = await add_pool(site, "Loket")
    end = SLOT_0900 + timedelta(minutes=30)
    async with site_uow(site).locked() as tx:
        await tx.set_pool_timezone(EVERYTHING, pool, "Europe/Amsterdam")
        rule = ScheduleRule(0, TimeWindow(time(9), time(12), 2, None, Offer.BOTH))
        await tx.add_pool_rule(EVERYTHING, pool, rule)
        group = await tx.get_group("demo")
        assert group is not None
        slot = await tx.add_slot(
            Slot(
                None,
                group.id,
                SLOT_0900,
                end,
                2,
                BookingKind.SCHEDULED,
                pool,
                end + timedelta(minutes=5),
            )
        )
        for number in range(appointments):
            await tx.add_booking(
                confirmed_booking(
                    id=None,
                    group_id=group.id,
                    slot_id=slot.id,
                    token_hash=f"{number + 1:064d}",
                    public_ref=f"ref{number + 1:020d}",
                )
            )
    site.clock.advance(SLOT_0900 + timedelta(hours=4) + CLOSE_DELAY - site.clock.now())


async def test_the_organisation_admin_sees_the_pool_with_its_measures(site: Site) -> None:
    await staffed_pool_with_appointments(site)
    await site.login_as(ORG_ADMIN)

    page = await site.client.get(PAGE)

    assert page.status_code == 200
    assert 'aria-labelledby="pools-heading"' in page.text
    row = page.text.split('id="pools-heading"')[1]
    assert "Loket" in row
    assert "<td>6.0</td>" in row  # two units for three hours
    assert "<td>6</td>" in row  # six appointments
    assert "<td>4.0</td>" in row  # the first hour holds 6 x 35 minutes, capped at its 2 unit-hours
    assert "33%" in row


async def test_a_pool_with_few_appointments_shows_only_its_staffing(site: Site) -> None:
    await staffed_pool_with_appointments(site, appointments=2)
    await site.login_as(SUPER)

    row = (await site.client.get(PAGE)).text.split('id="pools-heading"')[1]

    assert "<td>6.0</td>" in row
    assert "33%" not in row
    assert "niet beschikbaar" in row


async def test_the_pool_is_on_the_organisations_page_only(site: Site) -> None:
    await staffed_pool_with_appointments(site)
    await site.login_as(GROUP_ADMIN)

    group_page = await site.client.get(f"/admin/groups/demo/usage?view=capacity&{RANGE}")
    org_page = await site.client.get(PAGE)

    assert 'id="pools-heading"' not in group_page.text
    assert org_page.status_code == 403 or 'id="pools-heading"' not in org_page.text


async def test_another_organisations_admin_is_refused_and_sees_no_pool(site: Site) -> None:
    await staffed_pool_with_appointments(site)
    await site.login_as(OUTSIDER)

    page = await site.client.get(PAGE)

    assert page.status_code in (403, 404)
    assert "Loket" not in page.text


async def test_the_page_works_without_a_pool(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = await site.client.get(PAGE)

    assert page.status_code == 200
    assert 'id="pools-heading"' not in page.text
