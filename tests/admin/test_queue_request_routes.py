"""Request work in the own queue page, against SQLite (ADR 0121 step 5b, A19, A27, A35).

A booking of a flow without video on a counted pool of the demo organisation has no meeting:
the queue row shows no video state, the claimed page offers *Start* and then *Complete*
instead of a meeting link, and completing it returns to the queue with a confirmation.
"""

from datetime import time, timedelta

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow, SlotRow
from app.domain.authorization import Scope
from app.domain.scheduling import Offer, ScheduleRule, TimeWindow
from tests.admin.conftest import AGENT, SAME_ORIGIN, SUPER, Site
from tests.support.admin_site import site_uow
from tests.support.domain import FRIDAY_0900
from tests.support.flow_seed import add_flow, publish_flow
from tests.support.flows import engine_of, flow_ids
from tests.support.queue import own_queue
from tests.support.skill_seed import add_pool

QUEUE = "/admin/groups/demo/queue"
EVERYTHING = Scope(everything=True)


async def _request_work(site: Site) -> int:
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


async def test_request_work_is_started_and_completed_without_a_meeting(site: Site) -> None:
    booking_id = await _request_work(site)
    await site.login_as(AGENT)

    page = (await site.client.get(QUEUE)).text
    claimed = await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)
    opened = (await site.client.get(f"{QUEUE}/{booking_id}")).text

    assert "<td>Ada<br>" in page
    assert "Videolink wordt klaargezet" not in page
    assert claimed.status_code == 303
    assert "Dit verzoek heeft geen videogesprek." in opened
    assert f"{QUEUE}/{booking_id}/start" in opened
    assert f"{QUEUE}/{booking_id}/complete" not in opened
    assert "Deelnemen aan het gesprek" not in opened

    site.clock.advance(timedelta(minutes=4))
    started = await site.client.post(f"{QUEUE}/{booking_id}/start", headers=SAME_ORIGIN)
    running = (await site.client.get(f"{QUEUE}/{booking_id}/live")).text

    assert started.status_code == 303
    assert "Bezig sinds" in running
    assert f"{QUEUE}/{booking_id}/complete" in running
    assert f"{QUEUE}/{booking_id}/start" not in running

    done = await site.client.post(f"{QUEUE}/{booking_id}/complete", headers=SAME_ORIGIN)

    assert done.status_code == 303
    assert done.headers["location"].endswith("done=completed")
    async with AsyncSession(engine_of(site.client)) as session:
        status = await session.scalar(select(BookingRow.status).where(BookingRow.id == booking_id))
    assert status == "completed"


async def test_a_late_start_is_refused_with_a_message_on_the_list(site: Site) -> None:
    booking_id = await _request_work(site)
    await site.login_as(AGENT)
    await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)
    site.clock.advance(timedelta(minutes=5))

    late = await site.client.post(f"{QUEUE}/{booking_id}/start", headers=SAME_ORIGIN)
    early = await site.client.post(f"{QUEUE}/{booking_id}/complete", headers=SAME_ORIGIN)

    assert late.status_code == 409
    assert "te lang laten liggen" in late.text
    assert early.status_code == 409
    assert "nog niet begonnen" in early.text
