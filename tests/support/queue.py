"""An own queue with one booking at its start, for the queue and overview tests (ADR 0043).

``request_work`` puts that booking on a staffed counted pool under a flow without video
(ADR 0121 steps 5 and 7).
"""

from dataclasses import replace
from datetime import time, timedelta

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow, SlotRow
from app.domain.authorization import Scope
from app.domain.booking import MeetingRef
from app.domain.scheduling import DispatchKind, Offer, ScheduleRule, TimeWindow
from app.domain.services import BookingService
from tests.admin.conftest import SUPER, Site
from tests.support.admin_site import site_uow
from tests.support.domain import ADA, FRIDAY_0900, TEST_TOKEN_KEY, book_confirmed
from tests.support.flow_seed import add_flow, publish_flow
from tests.support.flows import engine_of, flow_ids
from tests.support.skill_seed import add_pool

EVERYTHING = Scope(everything=True)


async def own_queue(site: Site, *, meeting: bool = True, **changes: object) -> int:
    """Switch the demo group to the own queue, book Friday 09:00 and move to its start.

    ``meeting=False`` leaves the booking without a meeting (a pending or failed video link,
    ADR 0072).
    """
    uow = site_uow(site)
    async with uow.locked() as tx:
        summary = await tx.get_group_summary("demo")
        assert summary is not None
        group = replace(summary.group, dispatch_kind=DispatchKind.OPENPLANNER_QUEUE, **changes)
        await tx.save_group(group)
    receipt = await book_confirmed(
        BookingService(uow, site.clock, TEST_TOKEN_KEY, treatment_buffer=0),
        group_slug="demo",
        start=FRIDAY_0900,
        contact=ADA,
        locale="nl",
    )
    booking_id = receipt.booking.id or 0
    if meeting:
        async with uow.locked() as tx:
            await tx.save_meeting(booking_id, MeetingRef("m-1", "https://webex.example/j/1"))
    site.clock.advance(FRIDAY_0900 - site.clock.now() - timedelta(minutes=5))
    return booking_id


async def request_work(site: Site) -> int:
    """Book in the own queue and put the booking on a staffed pool under a flow without video."""
    booking_id = await own_queue(site, meeting=False, queue_free_pick_allowed=True)
    await site.login_as(SUPER)
    pool = await add_pool(site, "Loket")
    async with site_uow(site).locked() as tx:
        assert await tx.set_pool_timezone(EVERYTHING, pool, "Europe/Amsterdam")
        block = TimeWindow(time(8), time(17), 2, None, Offer.BOTH)
        assert await tx.add_pool_rule(EVERYTHING, pool, ScheduleRule(4, block)) is not None
    await add_flow(site, "advies", pool)
    await publish_flow(site)
    engine = engine_of(site.client)
    flow_id = (await flow_ids(engine))["advies"]
    async with AsyncSession(engine) as session, session.begin():
        group_id = await session.scalar(
            select(BookingRow.group_id).where(BookingRow.id == booking_id)
        )
        slot_id = (
            await session.execute(
                insert(SlotRow)
                .values(
                    group_id=group_id,
                    start=FRIDAY_0900,
                    end=FRIDAY_0900 + timedelta(minutes=30),
                    capacity_snapshot=2,
                    kind="scheduled",
                    capacity_pool_id=pool,
                    occupied_until=FRIDAY_0900 + timedelta(minutes=30),
                )
                .returning(SlotRow.id)
            )
        ).scalar_one()
        await session.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(slot_id=slot_id, flow_id=flow_id, flow_revision=1)
        )
    return booking_id
