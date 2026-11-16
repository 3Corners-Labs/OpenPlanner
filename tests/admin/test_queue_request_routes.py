"""Request work in the own queue page, against SQLite (ADR 0121 step 5b, A19, A27, A35).

A booking of a flow without video on a counted pool of the demo organisation has no meeting:
the queue row shows no video state, the claimed page offers *Start* and then *Complete*
instead of a meeting link, and completing it returns to the queue with a confirmation.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow
from app.domain.authorization import Scope
from app.domain.request_watch import RequestWatch
from app.domain.service_requests import WorkChannel, WorkItem, WorkItemState, appointment_request
from tests.support.admin_site import AGENT, GROUP_ADMIN, SAME_ORIGIN, Site, site_uow
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
    async with AsyncSession(site.app.state.engine) as session:
        status = await session.scalar(select(BookingRow.status).where(BookingRow.id == booking_id))
    assert status == "completed"


async def test_video_request_work_shows_its_meeting_beside_start_and_complete(
    site: Site,
) -> None:
    """ADR 0121 row 36: a video flow's request work has its join link and the participant."""
    booking_id = await request_work(site, video=True)
    await site.login_as(AGENT)

    await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)
    opened = (await site.client.get(f"{QUEUE}/{booking_id}")).text
    site.clock.advance(timedelta(minutes=4))
    await site.client.post(f"{QUEUE}/{booking_id}/start", headers=SAME_ORIGIN)
    running = (await site.client.get(f"{QUEUE}/{booking_id}/live")).text

    assert "Dit verzoek heeft geen videogesprek." not in opened
    assert 'href="https://webex.example/j/1"' in opened
    assert f"{QUEUE}/{booking_id}/start" in opened
    assert "data-live-status" in opened
    assert 'href="https://webex.example/j/1"' in running
    assert f"{QUEUE}/{booking_id}/complete" in running


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
    await RequestWatch(site_uow(site), site.clock, 24).run()

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


async def _hold(site: Site, booking_id: int) -> int:
    """Hand the request of ``booking_id`` to the contact center, as a sent attempt; its id."""
    async with site_uow(site).locked() as tx:
        booking = await tx.get_booking(booking_id)
        group = await tx.get_group_by_id(booking.group_id) if booking else None
        assert booking is not None
        assert group is not None
        request = await tx.add_service_request(
            appointment_request(
                organisation_id=group.organisation_id,
                group_id=booking.group_id,
                flow=booking.flow,  # type: ignore[arg-type]
                booking_id=booking_id,
                booking_end=booking.end,
                now=site.clock.now(),
                unresolved_hours=24,
            )
        )
        await tx.add_work_item(
            WorkItem(
                None,
                request.id or 0,
                WorkItemState.DISPATCHED,
                None,
                site.clock.now(),
                None,
                channel=WorkChannel.CONTACT_CENTER,
            )
        )
    return request.id or 0


async def _state(site: Site, booking_id: int, request_id: int) -> tuple[object, ...]:
    """Return what a refused claim or check-in must leave alone."""
    async with site_uow(site).read() as tx:
        booking = await tx.get_booking(booking_id)
        request = await tx.get_service_request(request_id)
        items = await tx.work_items_of(request_id)
    assert booking is not None
    assert request is not None
    return (
        booking.claim,
        request.handling,
        request.arrived_at,
        [(i.state, i.channel) for i in items],
    )


async def test_work_the_contact_center_holds_is_shown_as_held_without_a_claim(
    site: Site,
) -> None:
    """ADR 0121 step 28: the chip says who holds it, in words; no *Claim* for anyone."""
    booking_id = await request_work(site)
    await _hold(site, booking_id)
    await site.login_as(AGENT)

    page = (await site.client.get(QUEUE)).text
    english = (await site.client.get(f"{QUEUE}?lang=en")).text

    assert "Bij het contactcenter" in page
    assert "Held by the contact center" in english
    assert f"{QUEUE}/{booking_id}/claim" not in page
    assert f"{QUEUE}/next" not in page


async def test_a_direct_claim_or_check_in_of_held_work_is_refused_and_changes_nothing(
    site: Site,
) -> None:
    """The server decides, not the template: a hand-made POST is refused with the reason."""
    booking_id = await request_work(site)
    request_id = await _hold(site, booking_id)
    before = await _state(site, booking_id, request_id)
    await site.login_as(AGENT)

    claimed = await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)
    checked = await site.client.post(f"{QUEUE}/{booking_id}/check-in", headers=SAME_ORIGIN)

    for refused in (claimed, checked):
        assert refused.status_code == 409
        assert "Dit werk ligt bij het contactcenter" in refused.text
    assert await _state(site, booking_id, request_id) == before
