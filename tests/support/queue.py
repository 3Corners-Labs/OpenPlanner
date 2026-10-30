"""An own queue with one booking at its start, for the queue and overview tests (ADR 0043)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import MeetingRef
from app.domain.scheduling import DispatchKind
from app.domain.services import BookingService
from tests.admin.conftest import Site
from tests.support.domain import ADA, TEST_TOKEN_KEY

FRIDAY_0900 = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)  # demo group: open weekdays


def site_uow(site: Site) -> SqlUnitOfWork:
    """Return a unit of work on the site's database."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    return SqlUnitOfWork(app.state.engine)


async def own_queue(site: Site, **changes: object) -> int:
    """Switch the demo group to the own queue, book Friday 09:00 and move to its start."""
    uow = site_uow(site)
    async with uow.locked() as tx:
        summary = await tx.get_group_summary("demo")
        assert summary is not None
        group = replace(summary.group, dispatch_kind=DispatchKind.OPENPLANNER_QUEUE, **changes)
        await tx.save_group(group)
    receipt = await BookingService(uow, site.clock, TEST_TOKEN_KEY).book(
        group_slug="demo", start=FRIDAY_0900, contact=ADA, locale="nl"
    )
    booking_id = receipt.booking.id or 0
    async with uow.locked() as tx:
        await tx.save_meeting(booking_id, MeetingRef("m-1", "https://webex.example/j/1"))
    site.clock.advance(FRIDAY_0900 - site.clock.now() - timedelta(minutes=5))
    return booking_id
