"""An own queue with one booking at its start, for the queue and overview tests (ADR 0043)."""

from dataclasses import replace
from datetime import timedelta

from app.domain.booking import MeetingRef
from app.domain.scheduling import DispatchKind
from app.domain.services import BookingService
from tests.admin.conftest import Site
from tests.support.admin_site import site_uow
from tests.support.domain import ADA, FRIDAY_0900, TEST_TOKEN_KEY, book_confirmed


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
