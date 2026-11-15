"""The watchdog round and the deletion of request-level records (ADR 0121 step 5b).

On the in-memory store with the world of ``test_request_work``: lease expiry (back to the
queue), a booking cancelled elsewhere (cancelled), the deadline (expired, or recovery with the
unit kept, A33 and A34), a pool booking nobody claimed (an expired request), and the record of
a request without an appointment deleted on time, by the round and by the nightly sweep.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.calls import EventKind
from app.domain.contact_center import ContactCenterKind
from app.domain.flow_resolution import FlowRef
from app.domain.job_plan import JobType
from app.domain.ports import IntegrationError
from app.domain.request_watch import RequestWatch
from app.domain.retention_sweep import RetentionSweep
from app.domain.service_flows import FlowRevision, IntakeMode
from app.domain.service_requests import (
    HandlingState,
    PersonalDataState,
    RequestPersonalData,
    ServiceRequest,
    WorkItem,
    WorkItemState,
    watchdog_deadline,
)
from app.domain.webhook_replay import INSTALLATION_SCOPE
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryTransaction, InMemoryUnitOfWork
from tests.support.contact_center_requests import CC_FLOW, Desk, cc_world, desk
from tests.support.domain import NOW, SLOT_0900
from tests.support.request_work import (
    AGENT,
    LENGTH,
    POOL,
    VIDEO_FLOW,
    at,
    book,
    queue_of,
    world,
)

END = SLOT_0900 + LENGTH
DEADLINE = END + timedelta(hours=24)


def watch(uow: InMemoryUnitOfWork, clock: FixedClock) -> RequestWatch:
    """Return the round with 24 watchdog hours."""
    return RequestWatch(uow, clock, 24)


async def test_a_lease_that_ran_out_returns_the_work_to_the_queue() -> None:
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    at(clock)
    await queue_of(uow, clock).claim(AGENT, "demo", booking_id)
    at(clock, 5)

    result = await watch(uow, clock).run()

    request = next(iter(uow.state.requests.values()))
    assert result.changed == 1
    assert request.handling is HandlingState.QUEUED
    assert uow.state.bookings[booking_id].claim is None
    assert [i.state for i in uow.state.work_items.values()] == [WorkItemState.LEASE_EXPIRED]
    assert (await watch(uow, clock).run()).changed == 0


async def test_a_booking_cancelled_elsewhere_cancels_its_unstarted_request() -> None:
    """A previous image cancels the booking; the round sees it (rollback, ADR 0116)."""
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    at(clock)
    await queue_of(uow, clock).claim(AGENT, "demo", booking_id)
    booking = uow.state.bookings[booking_id]
    uow.state.bookings[booking_id] = replace(
        booking, status=BookingStatus.CANCELLED, cancelled_at=clock.now()
    )

    await watch(uow, clock).run()

    request = next(iter(uow.state.requests.values()))
    assert request.handling is HandlingState.CANCELLED
    assert request.resolved_at == clock.now()


async def test_started_work_past_the_deadline_is_marked_for_recovery_and_keeps_its_unit() -> None:
    """A34."""
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    at(clock)
    queue = queue_of(uow, clock)
    await queue.claim(AGENT, "demo", booking_id)
    await queue.start(AGENT, "demo", booking_id)
    clock.advance(DEADLINE - clock.now())

    await watch(uow, clock).run()

    request = next(iter(uow.state.requests.values()))
    assert request.handling is HandlingState.RECOVERY_REQUIRED
    async with uow.read() as tx:
        held = await tx.get_pool_commitments(POOL, clock.now(), clock.now() + timedelta(hours=1))
    assert [c.in_progress for c in held] == [True]  # the overrun still holds its unit
    assert uow.state.bookings[booking_id].claim is not None


async def test_a_pool_booking_nobody_claimed_gets_an_expired_request_once() -> None:
    """A33: never claimed, never checked in: expired at the deadline, then deleted."""
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    clock.advance(DEADLINE - timedelta(minutes=1) - clock.now())
    assert (await watch(uow, clock).run()).expired_unclaimed == 0

    clock.advance(timedelta(minutes=1))
    first = await watch(uow, clock).run()
    second = await watch(uow, clock).run()

    assert (first.expired_unclaimed, second.expired_unclaimed) == (1, 0)
    request = next(iter(uow.state.requests.values()))
    assert request.booking_id == booking_id
    assert request.handling is HandlingState.EXPIRED


async def test_a_legacy_booking_is_never_watched() -> None:
    uow, clock = world(), FixedClock(NOW)
    await book(uow, clock, None)
    clock.advance(DEADLINE + timedelta(days=1) - clock.now())

    await watch(uow, clock).run()

    assert uow.state.requests == {}


def on_demand() -> ServiceRequest:
    """Return a stored-shape request without an appointment of the demo service."""
    return ServiceRequest(
        id=None,
        organisation_id=1,
        group_id=1,
        flow=FlowRef(50, 1),
        intake=IntakeMode.ON_DEMAND,
        booking_id=None,
        handling=HandlingState.QUEUED,
        created_at=NOW,
        watchdog_at=watchdog_deadline(NOW, 24),
        pool_id=7,
    )


async def store_record(uow: InMemoryUnitOfWork) -> int:
    """Store a request without an appointment that holds a name (the writer is step 7)."""
    async with uow.locked() as tx:
        request = await tx.add_service_request(on_demand(), RequestPersonalData(first_name="Ada"))
    return request.id or 0


async def test_the_round_deletes_a_record_at_its_moment_and_not_before() -> None:
    """A20: the request stays valid without data; the record goes a day after the deadline."""
    uow, clock = world(), FixedClock(NOW)
    request_id = await store_record(uow)
    moment = watchdog_deadline(NOW, 24) + timedelta(hours=24)
    clock.advance(moment - timedelta(seconds=1) - NOW)
    assert (await watch(uow, clock).run()).deleted == 0

    clock.advance(timedelta(seconds=1))
    result = await watch(uow, clock).run()

    assert result.deleted == 1
    assert uow.state.request_data == {}
    assert uow.state.requests[request_id].personal_data is PersonalDataState.DELETED


async def test_the_sweep_deletes_an_overdue_record_the_round_missed_and_counts_it() -> None:
    uow, clock = world(), FixedClock(NOW)
    request_id = await store_record(uow)
    clock.advance(timedelta(days=4))

    found = await RetentionSweep(uow, clock).run(dry_run=True)
    result = await RetentionSweep(uow, clock).run()

    assert (found.overdue, found.purged) == (1, 0)
    assert found.backlog == 1
    assert (result.overdue, result.purged, result.failed) == (1, 1, 0)
    assert result.per_group == {"demo": 1}
    assert uow.state.requests[request_id].personal_data is PersonalDataState.DELETED


async def test_a_booking_on_hold_keeps_its_request_open_and_the_deadline_follows_a_new_time() -> (
    None
):
    """Review finding 2: suspended or awaiting a new time is no cancellation (rebook path)."""
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    queue = queue_of(uow, clock)
    at(clock)
    await queue.claim(AGENT, "demo", booking_id)
    booking = uow.state.bookings[booking_id]
    uow.state.bookings[booking_id] = replace(booking, status=BookingStatus.AWAITING_REBOOK)
    clock.advance(DEADLINE + timedelta(days=1) - clock.now())

    await watch(uow, clock).run()

    request = next(iter(uow.state.requests.values()))
    assert request.handling is HandlingState.QUEUED  # the lease ended; nothing else
    assert uow.state.bookings[booking_id].claim is None

    # The participant accepts a new time: the booking goes ahead a week later.
    new_end = clock.now() + timedelta(days=7)
    uow.state.bookings[booking_id] = replace(
        uow.state.bookings[booking_id],
        status=BookingStatus.CONFIRMED,
        start=new_end - LENGTH,
        end=new_end,
    )
    await watch(uow, clock).run()
    moved = uow.state.requests[request.id or 0]
    assert moved.watchdog_at == new_end + timedelta(hours=24)
    assert moved.handling is HandlingState.QUEUED

    clock.advance(new_end - LENGTH - clock.now())
    again = await queue.claim(AGENT, "demo", booking_id)
    assert again.work is not None
    assert again.work.request.id == request.id

    clock.advance(new_end + timedelta(hours=24) - clock.now())
    await watch(uow, clock).run()
    assert uow.state.requests[request.id or 0].handling is HandlingState.EXPIRED


async def test_a_video_booking_on_a_pool_never_gets_an_expired_request() -> None:
    """Review finding 3: the watchdog uses the queue's rule of request work."""
    uow, clock = world(), FixedClock(NOW)
    await book(uow, clock, VIDEO_FLOW)
    clock.advance(DEADLINE + timedelta(days=1) - clock.now())

    result = await watch(uow, clock).run()

    assert result.expired_unclaimed == 0
    assert uow.state.requests == {}


async def test_the_pseudonym_goes_on_time_also_without_a_record() -> None:
    """Review finding 1: a flow that collected nothing still names who took the work."""
    uow, clock = world(), FixedClock(NOW)
    async with uow.locked() as tx:
        request = await tx.add_service_request(on_demand())
        await tx.add_work_item(
            WorkItem(None, request.id or 0, WorkItemState.ASSIGNED, "agent", NOW, None)
        )
    clock.advance(watchdog_deadline(NOW, 24) + timedelta(hours=24) - NOW)

    result = await watch(uow, clock).run()

    assert result.deleted == 1
    assert [i.assigned_to for i in uow.state.work_items.values()] == [None]
    assert uow.state.requests[request.id or 0].personal_data is PersonalDataState.NOT_COLLECTED
    assert (await watch(uow, clock).run()).deleted == 0


async def test_a_failure_is_counted_with_its_type_name_only() -> None:
    uow, clock = world(), FixedClock(NOW)
    await store_record(uow)
    round_ = watch(uow, clock)

    async def broken(request_id: int, now: object) -> bool:
        raise ValueError("secret detail")

    round_._watch = broken  # type: ignore[method-assign]
    result = await round_.run()

    assert (result.failed, result.errors) == (1, ("ValueError",))


async def dispatched_attempt(*, ambiguous: bool = False) -> tuple[Desk, int]:
    """Return the desk with one contact-center attempt of a 09:00 booking, sent or in doubt."""
    uow, clock = cc_world(), FixedClock(NOW)
    the_desk = desk(uow, clock)
    booking_id = await book(uow, clock, CC_FLOW)
    if ambiguous:
        the_desk.dispatcher.error = IntegrationError("unreachable", retryable=True, ambiguous=True)
    booking = uow.state.bookings[booking_id]
    revision = await the_desk.dispatch.handles(booking)
    assert isinstance(revision, FlowRevision)
    await the_desk.dispatch.dispatch(booking, uow.state.groups[booking.group_id], revision)
    the_desk.dispatcher.error = None
    return the_desk, booking_id


def end_jobs(uow: InMemoryUnitOfWork, booking_id: int) -> int:
    """Count the booking's pending end-task jobs."""
    return sum(1 for j in uow.state.jobs.get(booking_id, []) if j.type is JobType.END_WXCC_TASK)


def contact_center_items(uow: InMemoryUnitOfWork) -> list[WorkItem]:
    """Return the contact-center attempts of the store."""
    return [i for i in uow.state.work_items.values() if i.channel.value == "contact_center"]


async def test_the_watchdog_ends_at_the_platform_the_task_of_an_attempt_it_expires() -> None:
    """Step 27 (E9): an attempt nobody connected by the deadline leaves no task with an agent.

    The round plans the end-task job once (a second round adds none); the job marks the task
    cancelled, ends it and says so on the booking's timeline, sending nothing but the task id
    and the reference.
    """
    the_desk, booking_id = await dispatched_attempt()
    uow, clock = the_desk.uow, the_desk.clock
    task = uow.state.bookings[booking_id].contact_center_task
    assert task is not None
    clock.advance(DEADLINE - clock.now())

    first = await watch(uow, clock).run()
    second = await watch(uow, clock).run()

    assert (first.tasks_ended, second.tasks_ended) == (1, 0)
    assert [i.state for i in contact_center_items(uow)] == [WorkItemState.EXPIRED]
    assert end_jobs(uow, booking_id) == 1
    booking = uow.state.bookings[booking_id]
    await the_desk.calls.end_task(booking, uow.state.groups[booking.group_id])
    assert the_desk.dispatcher.ended == [task.id]
    assert the_desk.dispatcher.marked == [(task.id, booking.reference)]
    kinds = [e.kind for e in uow.state.booking_events[booking_id]]
    assert EventKind.TASK_END_REQUESTED in kinds


async def test_a_refused_end_is_on_the_timeline_and_an_unreachable_platform_is_retried() -> None:
    """The job's failure is visible (ADR 0065): a refusal with its code, a retry by raising."""
    the_desk, booking_id = await dispatched_attempt()
    uow, clock = the_desk.uow, the_desk.clock
    clock.advance(DEADLINE - clock.now())
    await watch(uow, clock).run()
    booking = uow.state.bookings[booking_id]
    group = uow.state.groups[booking.group_id]

    the_desk.dispatcher.end_error = IntegrationError("unreachable", retryable=True)
    with pytest.raises(IntegrationError):
        await the_desk.calls.end_task(booking, group)
    the_desk.dispatcher.end_error = IntegrationError("task_not_found", retryable=False)
    await the_desk.calls.end_task(booking, group)

    events = uow.state.booking_events[booking_id]
    assert [(e.kind, e.detail) for e in events if e.kind is EventKind.END_NOT_POSSIBLE] == [
        (EventKind.END_NOT_POSSIBLE, "task_not_found")
    ]


async def test_a_cancelled_booking_has_its_task_ended_by_its_cancellation_not_twice() -> None:
    """The cancellation's own job ends the task (ADR 0021); the round plans no second one."""
    the_desk, booking_id = await dispatched_attempt()
    uow, clock = the_desk.uow, the_desk.clock
    uow.state.bookings[booking_id] = replace(
        uow.state.bookings[booking_id], status=BookingStatus.CANCELLED, cancelled_at=clock.now()
    )

    result = await watch(uow, clock).run()

    assert [i.state for i in contact_center_items(uow)] == [WorkItemState.CANCELLED]
    assert (result.tasks_ended, end_jobs(uow, booking_id)) == (0, 0)


async def test_a_booking_that_ended_otherwise_has_its_task_ended_by_the_round() -> None:
    """Marked a no-show, say: nothing else ends the task, so the round does."""
    the_desk, booking_id = await dispatched_attempt()
    uow, clock = the_desk.uow, the_desk.clock
    uow.state.bookings[booking_id] = replace(
        uow.state.bookings[booking_id], status=BookingStatus.NO_SHOW
    )

    result = await watch(uow, clock).run()

    assert [i.state for i in contact_center_items(uow)] == [WorkItemState.CANCELLED]
    assert (result.tasks_ended, end_jobs(uow, booking_id)) == (1, 1)


async def test_a_late_task_of_an_attempt_the_watchdog_expired_in_doubt_is_ended() -> None:
    """In doubt there is no task to end; one that shows up after the expiry is an orphan."""
    the_desk, booking_id = await dispatched_attempt(ambiguous=True)
    uow, clock = the_desk.uow, the_desk.clock
    clock.advance(DEADLINE - clock.now())

    result = await watch(uow, clock).run()

    assert result.tasks_ended == 0
    assert [i.state for i in contact_center_items(uow)] == [WorkItemState.EXPIRED]
    reference = uow.state.bookings[booking_id].reference
    await the_desk.integration.record_task_event(
        "late",
        EventKind.TASK_CREATED,
        clock.now(),
        origin=reference,
        provider=ContactCenterKind.WEBEX_CC,
        source="webex_cc",
        scope=INSTALLATION_SCOPE,
        body_identity="",
    )
    assert end_jobs(uow, booking_id) == 1
    assert [i.state for i in contact_center_items(uow)] == [WorkItemState.EXPIRED]


class CommitFails:
    """A store whose locked transactions all fail at the commit: nothing they wrote is kept."""

    def __init__(self, uow: InMemoryUnitOfWork) -> None:
        self._uow = uow

    def read(self) -> AbstractAsyncContextManager[InMemoryTransaction]:
        return self._uow.read()

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[InMemoryTransaction]:
        async with self._uow.read() as tx:  # a copy, thrown away
            yield tx
        raise RuntimeError("commit failed")


async def test_a_round_whose_commit_fails_reports_no_task_ended_and_plans_none() -> None:
    """The count says what was committed, never what was only written in a lost transaction."""
    the_desk, booking_id = await dispatched_attempt()
    uow, clock = the_desk.uow, the_desk.clock
    clock.advance(DEADLINE - clock.now())

    result = await RequestWatch(CommitFails(uow), clock, 24).run()  # type: ignore[arg-type]

    assert (result.tasks_ended, result.failed, result.errors) == (0, 1, ("RuntimeError",))
    assert end_jobs(uow, booking_id) == 0
    assert (await watch(uow, clock).run()).tasks_ended == 1  # the next round does it
