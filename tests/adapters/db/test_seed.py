"""Closure examples of the demo seed (ADR 0037): fixtures of the participant pages and e2e."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import (
    BROWSERS,
    CLOSED_GROUP_SLUG,
    JOURNEY_CALL_IN,
    JOURNEY_GROUP_SLUG,
    PLANNED_GROUP_SLUG,
    seed_closure_examples,
    seed_demo,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import Booking, BookingStatus
from app.domain.calls import JoinState, join_state
from app.domain.ports import Transaction
from app.domain.tokens import hash_token
from app.domain.trial_call import TrialState, trial_state
from tests.support.domain import NOW

KEY = b"k" * 32


async def _by_link(tx: Transaction, path: str) -> Booking:
    """Return the booking of a management link such as ``/b/<token>/test``."""
    token = path.removeprefix("/b/").split("/", 1)[0]
    booking = await tx.get_booking_by_token_hash(hash_token(token))
    assert booking is not None
    return booking


async def test_a_closed_team_a_planned_closure_and_a_suspended_booking(
    engine: AsyncEngine,
) -> None:
    await seed_demo(engine)

    examples = await seed_closure_examples(engine, NOW, KEY)

    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        closed = await tx.get_group(CLOSED_GROUP_SLUG)
        planned = await tx.get_group(PLANNED_GROUP_SLUG)
        assert closed is not None
        assert planned is not None
        (active,) = await tx.get_closures(closed.id, NOW)
        (upcoming,) = await tx.get_closures(planned.id, NOW)
        token = examples.suspended_booking.removeprefix("/b/")
        booking = await tx.get_booking_by_token_hash(hash_token(token))
        pending = [
            await tx.get_booking_by_token_hash(
                hash_token(path.removeprefix("/b/").removesuffix("/confirm"))
            )
            for path in (
                examples.pending_confirm,
                examples.pending_flow_chromium,
                examples.pending_flow_firefox,
                examples.pending_flow_webkit,
            )
        ]
    assert active.is_active(NOW)
    assert active.heading("en")
    assert active.contacts is not None
    assert active.image is not None  # the closure page with an image (CL-3)
    assert active.image.text.alt("en")
    assert upcoming.image is None  # and one without
    assert not upcoming.is_active(NOW)
    assert upcoming.is_visible(NOW)
    assert upcoming.starts_at - NOW > timedelta(days=1)
    assert booking is not None
    assert booking.status is BookingStatus.SUSPENDED
    assert booking.closure_id == active.id
    assert [b.status if b else None for b in pending] == [BookingStatus.PENDING_CONFIRMATION] * 4
    assert len({b.start for b in pending if b}) == 4  # one per slot
    assert examples.closed_group == f"/g/{CLOSED_GROUP_SLUG}"
    assert examples.planned_closure == f"/g/{PLANNED_GROUP_SLUG}/closure"


async def test_a_second_run_adds_only_a_new_booking_link(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    first = await seed_closure_examples(engine, NOW, KEY)

    second = await seed_closure_examples(engine, NOW, KEY)

    async with SqlUnitOfWork(engine).read() as tx:
        closed = await tx.get_group(CLOSED_GROUP_SLUG)
        assert closed is not None
        closures = await tx.get_closures(closed.id, NOW)
    assert len(closures) == 1
    assert second.suspended_booking != first.suspended_booking
    assert first.trial_page == f"{first.suspended_booking}/test"


async def test_the_journey_has_two_confirmed_bookings_per_browser(engine: AsyncEngine) -> None:
    await seed_demo(engine)

    examples = await seed_closure_examples(engine, NOW, KEY)

    assert set(examples.journeys) == set(BROWSERS)
    async with SqlUnitOfWork(engine).read() as tx:
        group = await tx.get_group(JOURNEY_GROUP_SLUG)
        assert group is not None
        assert group.video_enabled
        assert group.trial_call_enabled
        bookings = []
        for browser in BROWSERS:
            journey = examples.journeys[browser]
            trial = await _by_link(tx, journey.trial_page)
            call = await _by_link(tx, journey.call_page)
            bookings += [trial, call]
            admin = f"/admin/groups/{JOURNEY_GROUP_SLUG}/bookings"
            assert journey.trial_page.endswith("/test")
            assert journey.call_page.endswith("/join")
            assert journey.trial_booking == f"{admin}/{trial.id}"
            assert journey.call_booking == f"{admin}/{call.id}"
            # The trial call is on offer for the whole run; the join page is open at once.
            assert trial_state(trial, group, NOW) is TrialState.AVAILABLE
            assert trial.start > NOW + timedelta(days=1)
            assert call.start == NOW + JOURNEY_CALL_IN
            assert join_state(call, NOW) is JoinState.NO_MEETING
    assert all(b.status is BookingStatus.CONFIRMED and b.group_id == group.id for b in bookings)
    assert len({b.contact.email for b in bookings if b.contact}) == len(bookings)


async def test_the_journey_agenda_is_the_day_the_call_starts(engine: AsyncEngine) -> None:
    """Seeded just before midnight, the call falls on tomorrow's agenda, not on today's."""
    await seed_demo(engine)
    late = datetime(2026, 11, 1, 22, 55, tzinfo=UTC)  # 23:55 in Amsterdam, call at 00:05

    examples = await seed_closure_examples(engine, late, KEY)

    journey = examples.journeys["chromium"]
    assert journey.call_agenda == f"/admin/groups/{JOURNEY_GROUP_SLUG}/agenda?day=2026-11-02"
