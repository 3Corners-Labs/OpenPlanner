"""Meetings, tasks, timeline and call status in SQLite (ADR 0010, 0017, 0018)."""

from dataclasses import replace
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import MeetingRef
from app.domain.calls import BookingEvent, CallState, EventKind, TaskState, apply_event, new_status
from app.domain.scheduling import Offer
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import ADA, NOW, SLOT_0900, TEST_TOKEN_KEY, book


async def _booking_id(engine: AsyncEngine) -> int:
    await seed_group(engine, slug="aangifte")
    service = BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY)
    receipt = await book(service, SLOT_0900)
    assert receipt.booking.id is not None
    return receipt.booking.id


async def test_meeting_and_task_are_stored_and_found(engine: AsyncEngine) -> None:
    booking_id = await _booking_id(engine)
    uow = SqlUnitOfWork(engine)

    async with uow.locked() as tx:
        await tx.save_meeting(booking_id, MeetingRef("meeting-1", "https://site.webex.com/m/1"))
        await tx.save_task_id(booking_id, "task-1")
    async with uow.read() as tx:
        by_meeting = await tx.get_booking_by_meeting("meeting-1")
        by_task = await tx.get_booking_by_task("task-1")
        missing = await tx.get_booking_by_task("nope")

    assert by_meeting is not None
    assert by_meeting.meeting == MeetingRef("meeting-1", "https://site.webex.com/m/1")
    assert by_meeting.meeting.link == "https://site.webex.com/m/1"
    assert by_task is not None
    assert by_task.id == booking_id
    assert by_task.wxcc_task_id == "task-1"
    assert missing is None


async def test_purge_removes_the_join_link_but_keeps_the_ids(engine: AsyncEngine) -> None:
    booking_id = await _booking_id(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_meeting(booking_id, MeetingRef("meeting-1", "https://site.webex.com/m/1"))
        await tx.purge_personal_data(booking_id, NOW + timedelta(days=2))
    async with uow.read() as tx:
        booking = await tx.get_booking(booking_id)

    assert booking is not None
    assert booking.meeting == MeetingRef("meeting-1", None)
    assert booking.meeting.link is None


async def test_timeline_and_status_round_trip(engine: AsyncEngine) -> None:
    booking_id = await _booking_id(engine)
    uow = SqlUnitOfWork(engine)
    events = [
        BookingEvent(booking_id, NOW, EventKind.TASK_CREATED),
        BookingEvent(
            booking_id, NOW + timedelta(minutes=1), EventKind.TASK_FAILED, "ENTRY_POINT_NOT_FOUND"
        ),
    ]
    status = apply_event(
        apply_event(new_status(booking_id), EventKind.MEETING_STARTED, NOW),
        EventKind.TASK_CREATED,
        NOW,
    )

    async with uow.locked() as tx:
        for event in events:
            await tx.add_booking_event(event)
        await tx.save_call_status(status)
    async with uow.locked() as tx:
        await tx.save_call_status(apply_event(status, EventKind.HOST_JOINED, NOW))
    async with uow.read() as tx:
        stored_events = await tx.get_booking_events(booking_id)
        stored_status = await tx.get_call_status(booking_id)
        nothing = await tx.get_call_status(booking_id + 1)

    assert stored_events == events
    assert stored_status is not None
    assert (stored_status.call, stored_status.task) == (CallState.IN_PROGRESS, TaskState.QUEUED)
    assert stored_status.host_joined
    assert stored_status.started_at == NOW
    assert nothing is None


async def test_bookings_are_listed_per_day_within_the_scope(engine: AsyncEngine) -> None:
    from app.domain.authorization import Scope

    booking_id = await _booking_id(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_call_status(apply_event(new_status(booking_id), EventKind.TASK_CREATED, NOW))
        group = await tx.get_group("aangifte")
    assert group is not None
    lower = SLOT_0900 - timedelta(hours=9)
    upper = lower + timedelta(days=1)

    async with uow.read() as tx:
        everything = await tx.list_bookings(Scope(everything=True), group.id, lower, upper)
        own = await tx.list_bookings(Scope(groups=frozenset({"aangifte"})), group.id, lower, upper)
        other = await tx.list_bookings(Scope(groups=frozenset({"other"})), group.id, lower, upper)
        next_day = await tx.list_bookings(
            Scope(everything=True), group.id, upper, upper + timedelta(days=1)
        )

    assert [(row.booking.id, row.status.task) for row in everything] == [
        (booking_id, TaskState.QUEUED)
    ]
    assert [row.booking.id for row in own] == [booking_id]
    assert other == []
    assert next_day == []


async def test_webhook_events_are_claimed_once_and_forgotten(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)

    async with uow.locked() as tx:
        first = await tx.claim_webhook_event("webex", "a" * 64, NOW)
        again = await tx.claim_webhook_event("webex", "a" * 64, NOW)
        other_source = await tx.claim_webhook_event("wxcc", "a" * 64, NOW)
    async with uow.locked() as tx:
        await tx.claim_webhook_event("webex", "b" * 64, NOW + timedelta(days=2))
        forgotten = await tx.forget_webhook_events(NOW + timedelta(days=1))
        after = await tx.claim_webhook_event("webex", "a" * 64, NOW + timedelta(days=2))

    assert (first, again, other_source) == (True, False, True)
    assert forgotten == 2
    assert after is True


async def test_direct_calls_are_listed_with_their_status_and_cap(engine: AsyncEngine) -> None:
    await seed_group(engine, slug="aangifte", capacity=2)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        group = await tx.get_group("aangifte")
        assert group is not None
        await tx.save_group(replace(group, direct_call_max_concurrent=3))
        rules, _overrides = await tx.get_schedule_items(group.id)
        for rule in rules:  # ADR 0071: direct calls are offered per block
            await tx.update_schedule_rule(
                group.id, rule.id or 0, replace(rule, window=replace(rule.window, offer=Offer.BOTH))
            )
    clock = FixedClock(NOW)
    service = BookingService(uow, clock, TEST_TOKEN_KEY)
    await book(service, SLOT_0900)  # scheduled: not a direct call
    clock.advance(SLOT_0900 + timedelta(minutes=5) - NOW)
    first = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    second = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    first_id, second_id = first.booking.id or 0, second.booking.id or 0
    connected = apply_event(new_status(first_id), EventKind.TASK_CONNECTED, clock.now())
    async with uow.locked() as tx:
        await tx.save_call_status(connected)
        await tx.save_booking(second.booking.cancel(clock.now()))

    async with uow.read() as tx:
        calls = await tx.get_direct_calls(group.id, SLOT_0900)
        later = await tx.get_direct_calls(group.id, clock.now() + timedelta(minutes=1))
        stored = await tx.get_group("aangifte")

    assert [(c.booking_id, c.active, c.status.task) for c in calls] == [
        (first_id, True, TaskState.CONNECTED),
        (second_id, False, TaskState.NONE),
    ]
    assert calls[0].start == SLOT_0900 + timedelta(minutes=5)
    assert calls[0].end == calls[0].start + timedelta(minutes=30)
    assert later == []
    assert stored is not None
    assert stored.direct_call_max_concurrent == 3
