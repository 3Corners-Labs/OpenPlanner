"""One task, of one kind, per contact moment (ADR 0123 point 8, product owner 2026-11-16).

A booking reaches a staff member at the contact center as a work item (``form``) or as a
conversation (``conversation``). Both kinds may run in one installation, but one contact moment
gets exactly one task of exactly one kind, never both and never a second offer. Each test names
the hole of the audit it closes:

(a) whatever reads a task later (the end, adoption) follows the kind stored with the task or the
    attempt, never the routing as it is now;
(b) no create while the booking holds a task, or while an earlier create may still make one;
(c) a late task of the other kind is never attached; an orphan is ended as its own kind;
    the same for a booking without a request, whose unknown outcome is no longer retried.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.calls import EventKind
from app.domain.contact_center import TaskKind
from app.domain.contact_center_attempts import OUTCOME_UNKNOWN
from app.domain.integration import EventOutcome
from app.domain.ports import IntegrationError
from app.domain.request_dispatch import TASK_HELD
from app.domain.scheduling import DispatchKind
from app.domain.service_requests import WorkItemState
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.contact_center_requests import (
    AMBIGUOUS,
    CC_FLOW,
    WINDOW,
    Desk,
    cc_world,
    desk,
    dispatch,
    end_jobs,
    ensure,
    event,
    fenced,
    items_of,
    routed,
)
from tests.support.request_work import book


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return cc_world()


@pytest.fixture
def the_desk(uow: InMemoryUnitOfWork, clock: FixedClock) -> Desk:
    return desk(uow, clock, uses_call_page=True)


# (b) request work: no create while a task is held or may still come.


async def test_no_second_dispatch_while_a_fenced_attempt_may_still_make_its_task(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """The fallback ending gives the booking a fresh dispatch job; inside the window of the
    fenced attempt that job must not create a second task beside the one that may come."""
    booking_id = await book(uow, clock, CC_FLOW)
    await fenced(the_desk, booking_id)

    assert await dispatch(the_desk, booking_id) == TASK_HELD

    assert the_desk.dispatcher.items == []
    clock.advance(timedelta(minutes=WINDOW, seconds=1))  # its late task is no longer ours
    assert await dispatch(the_desk, booking_id) is None
    assert len(the_desk.dispatcher.items) == 1


async def test_no_second_dispatch_while_the_booking_holds_a_late_task(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A late task of a fenced attempt is kept to be ended; a new create would take its place
    on the booking and leave it with an agent, and the end job would end the wrong one."""
    booking_id = await book(uow, clock, CC_FLOW)
    await fenced(the_desk, booking_id)
    reference = uow.state.bookings[booking_id].reference
    await event(the_desk, "late-1", EventKind.TASK_CREATED, reference)
    clock.advance(timedelta(minutes=WINDOW + 1))

    assert await dispatch(the_desk, booking_id) == TASK_HELD

    assert the_desk.dispatcher.items == []
    task = uow.state.bookings[booking_id].contact_center_task
    assert task is not None
    assert task.id == "late-1"
    await end_jobs(the_desk, booking_id)
    assert the_desk.dispatcher.ended == ["late-1"]


# (c) the event's own kind.


async def test_a_late_task_of_the_other_kind_is_never_attached(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A conversation was tried and its outcome is unknown; a work item that names this
    booking is not that create's task. Its own task can still come and is adopted."""
    routed(uow, TaskKind.CONVERSATION)
    booking_id = await book(uow, clock, CC_FLOW)
    the_desk.dispatcher.error = AMBIGUOUS
    await dispatch(the_desk, booking_id)
    reference = uow.state.bookings[booking_id].reference

    outcome = await event(the_desk, "form-1", EventKind.TASK_CREATED, reference, TaskKind.FORM)

    assert outcome is EventOutcome.UNKNOWN
    assert uow.state.bookings[booking_id].contact_center_task is None
    [item] = items_of(uow, booking_id)
    assert item.state is WorkItemState.RECONCILIATION_REQUIRED
    await event(the_desk, "chat-1", EventKind.TASK_CONNECTED, reference, TaskKind.CONVERSATION)
    task = uow.state.bookings[booking_id].contact_center_task
    assert task is not None
    assert (task.id, task.task_kind) == ("chat-1", TaskKind.CONVERSATION)


async def test_an_orphan_is_kept_and_ended_as_the_kind_the_event_names(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A form was tried and fenced; the late task the platform reports is a conversation. It is
    ended as a conversation: at once, without the work item's cancellation mark."""
    booking_id = await book(uow, clock, CC_FLOW)
    await fenced(the_desk, booking_id)
    reference = uow.state.bookings[booking_id].reference

    await event(the_desk, "chat-2", EventKind.TASK_CREATED, reference, TaskKind.CONVERSATION)

    task = uow.state.bookings[booking_id].contact_center_task
    assert task is not None
    assert (task.id, task.task_kind) == ("chat-2", TaskKind.CONVERSATION)
    await end_jobs(the_desk, booking_id)
    assert (the_desk.dispatcher.marked, the_desk.dispatcher.ended) == ([], ["chat-2"])


# (a) the stored kind, never the routing of now.


async def test_a_task_is_ended_as_the_kind_it_was_made_after_the_routing_changed(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    routed(uow, TaskKind.CONVERSATION)
    booking_id = await book(uow, clock, CC_FLOW)
    assert await dispatch(the_desk, booking_id) is None
    routed(uow, TaskKind.FORM)
    booking = uow.state.bookings[booking_id]
    uow.state.bookings[booking_id] = replace(booking, status=BookingStatus.CANCELLED)

    await the_desk.calls.end_task(uow.state.bookings[booking_id], uow.state.groups[1])

    assert the_desk.dispatcher.kinds == [TaskKind.CONVERSATION]
    assert the_desk.dispatcher.marked == []  # a conversation is never marked
    assert the_desk.dispatcher.ended == ["task-1"]


# A booking without a request (the dispatch of ADR 0018): an unknown outcome is not retried.


async def legacy_booking(uow: InMemoryUnitOfWork, clock: FixedClock, kind: TaskKind) -> int:
    """Book a legacy appointment on a service that dispatches tasks of ``kind`` itself."""
    routed(uow, kind, dispatch_kind=DispatchKind.CONTACT_CENTER)
    return await book(uow, clock, None)


async def test_a_create_with_an_unknown_outcome_is_never_tried_again(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """The platform may have made the task: a retry would offer the appointment twice. The
    task that shows up later is the booking's own."""
    booking_id = await legacy_booking(uow, clock, TaskKind.FORM)
    the_desk.dispatcher.error = AMBIGUOUS

    await ensure(the_desk, booking_id)
    the_desk.dispatcher.error = None
    await ensure(the_desk, booking_id)  # the job again, or the fallback's fresh one

    assert the_desk.dispatcher.items == []
    assert uow.state.bookings[booking_id].attempted_task_kind is TaskKind.FORM
    details = [e.detail for e in uow.state.booking_events[booking_id]]
    assert OUTCOME_UNKNOWN in details
    reference = uow.state.bookings[booking_id].reference
    await event(the_desk, "late-2", EventKind.TASK_CONNECTED, reference)
    booking = uow.state.bookings[booking_id]
    assert booking.contact_center_task is not None
    assert (booking.contact_center_task.id, booking.attempted_task_kind) == ("late-2", None)


async def test_a_late_task_of_another_kind_is_not_the_one_that_was_tried(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await legacy_booking(uow, clock, TaskKind.CONVERSATION)
    the_desk.dispatcher.error = AMBIGUOUS
    await ensure(the_desk, booking_id)
    routed(uow, TaskKind.FORM, dispatch_kind=DispatchKind.CONTACT_CENTER)  # changed meanwhile
    reference = uow.state.bookings[booking_id].reference

    stray = await event(the_desk, "form-2", EventKind.TASK_CREATED, reference, TaskKind.FORM)
    await event(the_desk, "chat-3", EventKind.TASK_CREATED, reference)  # no kind in the event

    assert stray is EventOutcome.UNKNOWN
    task = uow.state.bookings[booking_id].contact_center_task
    assert task is not None
    assert (task.id, task.task_kind) == ("chat-3", TaskKind.CONVERSATION)


async def test_a_late_task_of_a_cancelled_booking_is_ended(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await legacy_booking(uow, clock, TaskKind.FORM)
    the_desk.dispatcher.error = AMBIGUOUS
    await ensure(the_desk, booking_id)
    booking = uow.state.bookings[booking_id]
    uow.state.bookings[booking_id] = replace(booking, status=BookingStatus.CANCELLED)

    await event(the_desk, "late-3", EventKind.TASK_CREATED, booking.reference)

    await end_jobs(the_desk, booking_id)
    assert the_desk.dispatcher.ended == ["late-3"]


async def test_a_create_that_failed_for_good_forgets_its_kind(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A refusal made no task: the job's retry may create, and nothing is left to adopt."""
    booking_id = await legacy_booking(uow, clock, TaskKind.FORM)
    the_desk.dispatcher.error = IntegrationError("rejected_400", retryable=False)

    with pytest.raises(IntegrationError):
        await ensure(the_desk, booking_id)

    assert uow.state.bookings[booking_id].attempted_task_kind is None
    the_desk.dispatcher.error = None
    await ensure(the_desk, booking_id)
    assert len(the_desk.dispatcher.items) == 1


async def test_an_unexpected_failure_of_the_create_forgets_its_kind(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """Any failure other than an unknown outcome clears the pending mark before it propagates,
    so the booking is never left marked as waiting for a task that no create is making."""
    booking_id = await legacy_booking(uow, clock, TaskKind.FORM)
    the_desk.dispatcher.error = RuntimeError("boom")  # type: ignore[assignment]

    with pytest.raises(RuntimeError):
        await ensure(the_desk, booking_id)

    assert uow.state.bookings[booking_id].attempted_task_kind is None
