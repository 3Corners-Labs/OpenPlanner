"""Request work in the own queue page, against SQLite (ADR 0121 step 5b, A19, A27, A35).

A booking of a flow without video on a counted pool of the demo organisation has no meeting:
the queue row shows no video state, the claimed page offers *Start* and then *Complete*
instead of a meeting link, and completing it returns to the queue with a confirmation.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.request_watch import RequestWatch
from tests.admin.conftest import AGENT, GROUP_ADMIN, SAME_ORIGIN, Site
from tests.support.flows import engine_of
from tests.support.queue import request_work

QUEUE = "/admin/groups/demo/queue"
EVERYTHING = Scope(everything=True)


async def test_request_work_is_started_and_completed_without_a_meeting(site: Site) -> None:
    booking_id = await request_work(site)
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
    booking_id = await request_work(site)
    await site.login_as(AGENT)
    await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)
    site.clock.advance(timedelta(minutes=5))

    late = await site.client.post(f"{QUEUE}/{booking_id}/start", headers=SAME_ORIGIN)
    early = await site.client.post(f"{QUEUE}/{booking_id}/complete", headers=SAME_ORIGIN)

    assert late.status_code == 409
    assert "te lang laten liggen" in late.text
    assert early.status_code == 409
    assert "nog niet begonnen" in early.text


async def test_a_supervisor_lists_and_completes_work_in_recovery(site: Site) -> None:
    """ADR 0121 step 5e: the appointment has ended, so only the recovery table shows it."""
    booking_id = await request_work(site)
    await site.login_as(AGENT)
    await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)
    await site.client.post(f"{QUEUE}/{booking_id}/start", headers=SAME_ORIGIN)
    site.clock.advance(timedelta(days=3))
    await RequestWatch(SqlUnitOfWork(engine_of(site.client)), site.clock, 24).run()

    agent_page = (await site.client.get(QUEUE)).text
    await site.login_as(GROUP_ADMIN)
    page = (await site.client.get(QUEUE)).text
    done = await site.client.post(f"{QUEUE}/{booking_id}/complete", headers=SAME_ORIGIN)
    after = (await site.client.get(QUEUE)).text

    assert "Werk in herstel" not in agent_page  # the list is for a supervisor of the queue
    assert "Werk in herstel" in page
    assert f"OP-{booking_id:06d}" in page
    assert f'action="{QUEUE}/{booking_id}/complete"' in page
    assert done.status_code == 303
    assert "Werk in herstel" not in after
