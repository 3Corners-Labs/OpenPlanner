"""Every state a booking can be left in has exactly one purge, at the moment the sweep uses.

The sweep (``RetentionSweep``) is the safety net under the ``purge_pii`` job. This test drives
the services through their transitions and checks the contract between the two: a booking that
holds personal data has one pending ``purge_pii`` job whose time is ``purge_moment`` (the same
moment, so the sweep never purges earlier than planned and never later). The one exception is
a suspended booking under an open-ended closure, which waits for a decision (WP-A2).
"""

from datetime import datetime, timedelta

from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.booking import Booking, BookingStatus
from app.domain.booking_changes import lapse, lapse_unconfirmed, request_rebook
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.job_plan import JobType, plan_for_direct
from app.domain.retention import PurgeCandidate, purge_moment
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import NOTICES
from tests.support.domain import ADA, SLOT_0900, TEST_TOKEN_KEY, book_confirmed, book_pending
from tests.support.principals import principal

GROUP_ADMIN = principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
NEXT_MONDAY = SLOT_0900 + timedelta(days=7)


def _closure_end(uow: InMemoryUnitOfWork, booking: Booking) -> datetime | None:
    closure = uow.state.closures.get(booking.closure_id) if booking.closure_id else None
    return closure.ends_at if closure else None


def assert_one_purge_at_the_retention_moment(uow: InMemoryUnitOfWork) -> set[BookingStatus]:
    """Check every booking that still holds personal data; return the statuses seen."""
    seen: set[BookingStatus] = set()
    for booking_id, booking in uow.state.bookings.items():
        if booking.contact is None:
            continue
        seen.add(booking.status)
        candidate = PurgeCandidate(
            booking.group_id,
            booking.status,
            booking.end,
            booking.cancelled_at,
            _closure_end(uow, booking),
            booking.rebook_by,
            booking.confirm_by,
        )
        purges = [j for j in uow.state.jobs.get(booking_id, []) if j.type is JobType.PURGE_PII]
        moment = purge_moment(candidate)
        if moment is None:
            assert purges == [], f"{booking.status}: open-ended closure plans no purge"
        else:
            assert [p.run_at for p in purges] == [moment], f"{booking.status}: purge mismatch"
    return seen


async def test_every_transition_leaves_one_purge_at_the_retention_moment(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    bookings = BookingService(uow, clock, TEST_TOKEN_KEY)
    closures = ClosureService(uow, clock, AdminService(uow, clock), bookings)
    seen: set[BookingStatus] = set()

    async def lapse_with(step, booking_id: int) -> None:  # type: ignore[no-untyped-def]
        async with uow.locked() as tx:
            booking = await tx.get_booking(booking_id)
            assert booking is not None
            await step(tx, booking, clock.now())

    pending = await book_pending(bookings, SLOT_0900, "demo")  # awaiting confirmation
    seen |= assert_one_purge_at_the_retention_moment(uow)
    await bookings.confirm(pending.management_token)  # confirmed
    seen |= assert_one_purge_at_the_retention_moment(uow)
    moved = await bookings.reschedule(pending.management_token, NEXT_MONDAY)  # new slot
    seen |= assert_one_purge_at_the_retention_moment(uow)
    await bookings.cancel(moved.management_token)  # cancelled
    seen |= assert_one_purge_at_the_retention_moment(uow)

    lapsing = await book_pending(bookings, SLOT_0900, "demo-twee")
    await lapse_with(lapse_unconfirmed, lapsing.booking.id or 0)  # unconfirmed lapses
    seen |= assert_one_purge_at_the_retention_moment(uow)

    waiting = await book_confirmed(
        bookings, group_slug="demo-twee", start=NEXT_MONDAY, contact=ADA, locale="nl"
    )
    async with uow.locked() as tx:
        booking = await tx.get_booking(waiting.booking.id or 0)
        assert booking is not None
        from app.domain.booking import ChangeReason

        await request_rebook(
            tx, booking, clock.now(), reason=ChangeReason.STAFFING, rebook_days=14, reminder_days=3
        )
    seen |= assert_one_purge_at_the_retention_moment(uow)

    await book_confirmed(bookings, group_slug="demo", start=NEXT_MONDAY, contact=ADA, locale="nl")
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, NEXT_MONDAY, NOTICES))
    seen |= assert_one_purge_at_the_retention_moment(uow)  # suspended, closure with an end
    await closures.lift(GROUP_ADMIN, "demo", reinstate=False)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))
    seen |= assert_one_purge_at_the_retention_moment(uow)  # suspended, open-ended: no purge
    await closures.lift(GROUP_ADMIN, "demo", reinstate=False)
    seen |= assert_one_purge_at_the_retention_moment(uow)  # the closure ended: purge planned

    clock.advance(uow.state.bookings[waiting.booking.id or 0].rebook_by - clock.now())  # type: ignore[operator]
    await lapse_with(lapse, waiting.booking.id or 0)  # request lapses
    seen |= assert_one_purge_at_the_retention_moment(uow)

    assert seen >= {
        BookingStatus.PENDING_CONFIRMATION,
        BookingStatus.CONFIRMED,
        BookingStatus.CANCELLED,
        BookingStatus.SUSPENDED,
        BookingStatus.AWAITING_REBOOK,
    }


def test_a_direct_call_is_purged_a_day_after_its_slot() -> None:
    # ``start_direct`` needs a staffed block with direct calls; its plan is checked here.
    end = NEXT_MONDAY + timedelta(minutes=30)
    [purge] = [
        j for j in plan_for_direct(start=NEXT_MONDAY, end=end) if j.type is JobType.PURGE_PII
    ]

    assert purge.run_at == purge_moment(PurgeCandidate(1, BookingStatus.CONFIRMED, end, None, None))
