"""Closure examples of the demo seed (ADR 0037): fixtures of the participant pages and e2e."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import (
    BROWSERS,
    CALL_GROUP_SLUG,
    CLOSED_GROUP_SLUG,
    DEMO_SLUG,
    FIRST_WEEKEND_DAY,
    JOURNEY_CALL_IN,
    JOURNEY_GROUP_SLUG,
    PENDING_TIMES,
    PLANNED_GROUP_SLUG,
    seed_closure_examples,
    seed_demo,
    seeded_group,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.availability import utc_bounds
from app.domain.booking import Booking, BookingStatus
from app.domain.booking_changes import planner_may_change, planner_may_request_rebook
from app.domain.calls import JoinState, join_state
from app.domain.ports import Transaction
from app.domain.scheduling import JoinClient
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
        demo = await tx.get_group("demo")
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
    first = pending[0]
    assert first is not None
    assert demo is not None
    assert examples.demo_agenda == (
        f"/admin/groups/demo/agenda?day={first.start.astimezone(demo.tz).date()}"
    )
    assert examples.correct_details == examples.pending_confirm.replace("/confirm", "/details")
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


async def test_the_call_pages_booking_belongs_to_a_team_that_chose_the_page(
    engine: AsyncEngine,
) -> None:
    await seed_demo(engine)

    examples = await seed_closure_examples(engine, NOW, KEY)

    async with SqlUnitOfWork(engine).read() as tx:
        call = await tx.get_group(CALL_GROUP_SLUG)
        journey = await tx.get_group(JOURNEY_GROUP_SLUG)
        booking = await _by_link(tx, examples.call_page)
    assert call is not None
    assert journey is not None
    assert call.join_client is JoinClient.EMBEDDED
    assert call.video_enabled
    assert journey.join_client is JoinClient.BROWSER  # the journey keeps the redirect
    assert booking.group_id == call.id


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


LATE_MONDAY = datetime(2026, 11, 2, 22, 55, tzinfo=UTC)
"""23:55 in Amsterdam: the edge where "today" and "the next weekday" turn over."""
SEED_MOMENTS = [
    *(LATE_MONDAY + timedelta(days=days) for days in range(7)),
    datetime(2027, 3, 26, 22, 30, tzinfo=UTC),  # Friday 23:30; the clocks go forward on Sunday
    datetime(2026, 10, 24, 22, 30, tzinfo=UTC),  # Sunday 00:30; the clocks go back at 03:00
]
"""A run of seed-closures late on each day of a week, and just before each clock change."""


@pytest.mark.parametrize("now", SEED_MOMENTS, ids=lambda now: f"{now:%a %Y-%m-%d %H:%M}Z")
async def test_the_demo_agenda_holds_every_planner_action_whatever_the_day(
    engine: AsyncEngine, now: datetime
) -> None:
    """The agenda check (e2e/tests/admin-agenda.spec.ts) opens this day, whatever the date.

    It is the next weekday, and it holds every booking awaiting confirmation (confirm, move,
    cancel) and the confirmed booking (move, propose or ask for a new time, cancel), each still
    open to a planner's change, so every row action is on the page.
    """
    await seed_demo(engine)

    examples = await seed_closure_examples(engine, now, KEY)

    async with SqlUnitOfWork(engine).read() as tx:
        group = await seeded_group(tx, DEMO_SLUG)
        day = date.fromisoformat(examples.demo_agenda.partition("?day=")[2])
        bookings = await tx.find_agenda_bookings(group.id, *utc_bounds(group, day, day))
    today = group.today(now)
    assert examples.demo_agenda == f"/admin/groups/{DEMO_SLUG}/agenda?day={day}"
    assert day > today
    assert day.weekday() < FIRST_WEEKEND_DAY
    assert all(
        (today + timedelta(days=n)).weekday() >= FIRST_WEEKEND_DAY
        for n in range(1, (day - today).days)
    )
    pending = [b for b in bookings if b.awaits_confirmation]
    confirmed = [b for b in bookings if b.is_active]
    assert len(pending) == len(PENDING_TIMES)
    assert all(planner_may_change(b, group, now) for b in pending)
    assert len(confirmed) == 1
    assert all(planner_may_request_rebook(b, group, now) for b in confirmed)


async def test_the_call_page_example_is_a_confirmed_booking_reached_by_its_link(
    engine: AsyncEngine,
) -> None:
    await seed_demo(engine)

    examples = await seed_closure_examples(engine, NOW, KEY)

    assert examples.call_page.startswith("/b/")
    assert examples.call_page.endswith("/call")
    async with SqlUnitOfWork(engine).read() as tx:
        booking = await _by_link(tx, examples.call_page)
    assert booking.status is BookingStatus.CONFIRMED
