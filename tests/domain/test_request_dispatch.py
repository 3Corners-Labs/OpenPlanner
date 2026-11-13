"""Request work at the contact center: dispatch, fence, reconcile, events (ADR 0121 step 6b).

Acceptance A21 (a task without a meeting, organisation credentials) and A22 (a timeout, the
fallback and a late callback: one fenced authority), on the demo service of
``tests.support.contact_center_requests``.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.calls import EventKind
from app.domain.contact_center import ContactCenterKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import EventOutcome
from app.domain.job_plan import JobType
from app.domain.ports import IntegrationError
from app.domain.request_dispatch import IN_DOUBT_SKIP, OWN_QUEUE
from app.domain.service_flows import FlowRevision, InteractionKind
from app.domain.service_requests import (
    HandlingState,
    ServiceRequest,
    WorkChannel,
    WorkItem,
    WorkItemState,
)
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.contact_center_requests import CC_FLOW, WINDOW, Desk, cc_world, desk
from tests.support.domain import NOW
from tests.support.request_work import AGENT, FLOW, at, book

SOURCE = "webex_cc"


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return cc_world()


@pytest.fixture
def the_desk(uow: InMemoryUnitOfWork, clock: FixedClock) -> Desk:
    return desk(uow, clock)


def request_of(uow: InMemoryUnitOfWork, booking_id: int) -> ServiceRequest:
    """Return the one request of ``booking_id``."""
    return next(r for r in uow.state.requests.values() if r.booking_id == booking_id)


def items_of(uow: InMemoryUnitOfWork, booking_id: int) -> list[WorkItem]:
    """Return the work items of the request of ``booking_id``, oldest first."""
    request = request_of(uow, booking_id)
    found = [i for i in uow.state.work_items.values() if i.request_id == request.id]
    return sorted(found, key=lambda i: i.id or 0)


async def dispatch(the_desk: Desk, booking_id: int) -> str | None:
    """Run the dispatch of ``booking_id`` as the dispatch job does."""
    booking = the_desk.uow.state.bookings[booking_id]
    group = the_desk.uow.state.groups[booking.group_id]
    revision = await the_desk.dispatch.handles(booking)
    assert isinstance(revision, FlowRevision)
    return await the_desk.dispatch.dispatch(booking, group, revision)


async def event(the_desk: Desk, task_id: str, kind: EventKind, origin: str = "") -> EventOutcome:
    """Deliver one platform event of ``task_id``."""
    return await the_desk.integration.record_task_event(
        task_id,
        kind,
        the_desk.clock.now(),
        origin=origin,
        provider=ContactCenterKind.WEBEX_CC,
        source=SOURCE,
    )


async def test_a_task_without_a_meeting_reaches_the_contact_center_once(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A21: the flow's interaction kind, no meeting link, the task kept on the booking."""
    booking_id = await book(uow, clock, CC_FLOW)

    assert await dispatch(the_desk, booking_id) is None

    [task] = the_desk.dispatcher.items
    assert task.interaction is InteractionKind.NONE
    assert task.meeting_link == ""
    [item] = items_of(uow, booking_id)
    assert (item.channel, item.state) == (WorkChannel.CONTACT_CENTER, WorkItemState.DISPATCHED)
    assert request_of(uow, booking_id).handling is HandlingState.ASSIGNED
    assert uow.state.bookings[booking_id].contact_center_task is not None
    assert await dispatch(the_desk, booking_id) is not None  # the job again: no second task
    assert len(the_desk.dispatcher.items) == 1


async def test_a_flow_on_the_own_queue_is_never_dispatched(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock, FLOW)

    assert await dispatch(the_desk, booking_id) == OWN_QUEUE
    assert the_desk.dispatcher.items == []


async def test_the_own_queue_and_the_contact_center_never_hold_one_request(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """The fence: whoever comes first holds the request; the other is refused or skips."""
    first = await book(uow, clock, CC_FLOW)
    second = await book(uow, clock, CC_FLOW)
    at(clock)

    await the_desk.queue.claim(AGENT, "demo", first)
    assert await dispatch(the_desk, first) == OWN_QUEUE

    assert await dispatch(the_desk, second) is None
    with pytest.raises(DomainError) as excinfo:
        await the_desk.queue.claim(AGENT, "demo", second)
    assert excinfo.value.code is ErrorCode.ALREADY_CLAIMED
    assert len(the_desk.dispatcher.items) == 1
    for booking_id in (first, second):
        assert len([i for i in items_of(uow, booking_id) if i.active]) == 1


async def test_a_refused_task_queues_the_request_and_the_job_retries(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock, CC_FLOW)
    the_desk.dispatcher.error = IntegrationError("unreachable", retryable=True)

    with pytest.raises(IntegrationError):
        await dispatch(the_desk, booking_id)

    [item] = items_of(uow, booking_id)
    assert item.state is WorkItemState.FAILED
    assert request_of(uow, booking_id).handling is HandlingState.QUEUED
    the_desk.dispatcher.error = None
    assert await dispatch(the_desk, booking_id) is None  # the retry opens a new attempt
    assert [i.state for i in items_of(uow, booking_id)] == [
        WorkItemState.FAILED,
        WorkItemState.DISPATCHED,
    ]


async def test_an_unknown_outcome_is_never_retried_and_blocks_the_own_queue(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A22, first half: a timeout leaves the attempt in doubt; no second task, no claim."""
    booking_id = await book(uow, clock, CC_FLOW)
    the_desk.dispatcher.error = IntegrationError("unreachable", retryable=True, ambiguous=True)

    assert await dispatch(the_desk, booking_id) == IN_DOUBT_SKIP

    [item] = items_of(uow, booking_id)
    assert item.state is WorkItemState.RECONCILIATION_REQUIRED
    the_desk.dispatcher.error = None
    assert await dispatch(the_desk, booking_id) is not None
    assert the_desk.dispatcher.items == []
    at(clock)
    with pytest.raises(DomainError) as excinfo:
        await the_desk.queue.claim(AGENT, "demo", booking_id)
    assert excinfo.value.code is ErrorCode.ALREADY_CLAIMED


async def test_a_late_task_of_an_attempt_in_doubt_is_adopted(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A22: the platform made the task after all; it becomes the attempt's task."""
    booking_id = await book(uow, clock, CC_FLOW)
    the_desk.dispatcher.error = IntegrationError("unreachable", retryable=True, ambiguous=True)
    await dispatch(the_desk, booking_id)
    reference = uow.state.bookings[booking_id].reference

    assert await event(the_desk, "late-1", EventKind.TASK_CONNECTED, reference) is (
        EventOutcome.RECORDED
    )

    [item] = items_of(uow, booking_id)
    assert item.state is WorkItemState.IN_PROGRESS
    task = uow.state.bookings[booking_id].contact_center_task
    assert task is not None
    assert task.id == "late-1"
    assert await the_desk.dispatch.fence_round() == 0


async def test_an_attempt_in_doubt_is_fenced_and_a_later_task_is_ended(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A22: after the window the own queue holds the request; a late task is ended."""
    booking_id = await book(uow, clock, CC_FLOW)
    the_desk.dispatcher.error = IntegrationError("unreachable", retryable=True, ambiguous=True)
    await dispatch(the_desk, booking_id)

    clock.advance(timedelta(minutes=WINDOW - 1))
    assert await the_desk.dispatch.fence_round() == 0
    clock.advance(timedelta(minutes=1))
    assert await the_desk.dispatch.fence_round() == 1

    [item] = items_of(uow, booking_id)
    assert item.state is WorkItemState.FENCED
    assert request_of(uow, booking_id).handling is HandlingState.QUEUED
    reference = uow.state.bookings[booking_id].reference
    await event(the_desk, "late-2", EventKind.TASK_CREATED, reference)
    assert any(j.type is JobType.END_WXCC_TASK for j in uow.state.jobs[booking_id])
    assert not [i for i in items_of(uow, booking_id) if i.active]
    at(clock)
    claimed = await the_desk.queue.claim(AGENT, "demo", booking_id)  # the one authority
    assert claimed.work is not None


async def test_an_origin_that_names_no_attempt_in_doubt_is_not_ours(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A forged or foreign origin attaches nothing to someone else's booking."""
    booking_id = await book(uow, clock, CC_FLOW)
    await dispatch(the_desk, booking_id)
    reference = uow.state.bookings[booking_id].reference

    for origin in (reference, "OP-999999", "Ada", ""):
        assert await event(the_desk, "stray", EventKind.TASK_CONNECTED, origin) is (
            EventOutcome.UNKNOWN
        )
    task = uow.state.bookings[booking_id].contact_center_task
    assert task is not None
    assert task.id != "stray"


async def test_platform_events_start_and_complete_the_attempt(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock, CC_FLOW)
    await dispatch(the_desk, booking_id)
    task = uow.state.bookings[booking_id].contact_center_task
    assert task is not None
    at(clock)

    await event(the_desk, task.id, EventKind.TASK_CONNECTED)
    [item] = items_of(uow, booking_id)
    assert item.state is WorkItemState.IN_PROGRESS
    assert item.started_at == clock.now()
    assert item.planned_until is not None

    clock.advance(timedelta(minutes=20))
    await event(the_desk, task.id, EventKind.TASK_ENDED)
    [item] = items_of(uow, booking_id)
    assert (item.state, item.ended_at) == (WorkItemState.COMPLETED, clock.now())
    assert request_of(uow, booking_id).handling is HandlingState.COMPLETED
    assert uow.state.bookings[booking_id].status is BookingStatus.COMPLETED


async def test_a_failed_task_goes_back_to_the_own_queue(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock, CC_FLOW)
    await dispatch(the_desk, booking_id)
    task = uow.state.bookings[booking_id].contact_center_task
    assert task is not None

    await event(the_desk, task.id, EventKind.TASK_FAILED)

    [item] = items_of(uow, booking_id)
    assert item.state is WorkItemState.FAILED
    assert request_of(uow, booking_id).handling is HandlingState.QUEUED


async def test_an_event_during_the_create_never_ends_the_task_it_creates(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """``task:new`` may arrive before the create answers: the attempt is not adopted then."""
    booking_id = await book(uow, clock, CC_FLOW)
    reference = uow.state.bookings[booking_id].reference
    sent = the_desk.dispatcher.create_task

    async def create_with_early_event(task: object) -> str:
        task_id = await sent(task)  # type: ignore[arg-type]
        assert await event(the_desk, task_id, EventKind.TASK_CREATED, reference) is (
            EventOutcome.UNKNOWN
        )
        return task_id

    the_desk.dispatcher.create_task = create_with_early_event  # type: ignore[method-assign]

    assert await dispatch(the_desk, booking_id) is None

    [item] = items_of(uow, booking_id)
    assert item.state is WorkItemState.DISPATCHED
    assert JobType.END_WXCC_TASK not in [j.type for j in uow.state.jobs.get(booking_id, [])]


async def test_a_late_task_is_only_ours_within_the_window_and_the_organisation(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock, CC_FLOW)
    the_desk.dispatcher.error = IntegrationError("unreachable", retryable=True, ambiguous=True)
    await dispatch(the_desk, booking_id)
    reference = uow.state.bookings[booking_id].reference

    foreign = await the_desk.integration.record_task_event(
        "other-org",
        EventKind.TASK_CONNECTED,
        clock.now(),
        origin=reference,
        provider=ContactCenterKind.WEBEX_CC,
        source=SOURCE,
        organisation_id=2,
    )
    assert foreign is EventOutcome.UNKNOWN
    clock.advance(timedelta(minutes=WINDOW))
    assert await the_desk.dispatch.fence_round() == 1
    clock.advance(timedelta(minutes=WINDOW + 1))
    assert await event(the_desk, "too-late", EventKind.TASK_CREATED, reference) is (
        EventOutcome.UNKNOWN
    )
    assert uow.state.bookings[booking_id].contact_center_task is None


async def test_a_task_that_cannot_be_built_opens_no_attempt(
    the_desk: Desk, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await book(uow, clock, CC_FLOW)
    uow.state.flow_revisions[(53, 1)] = replace(
        uow.state.flow_revisions[(53, 1)], interaction_kind=InteractionKind.TELEPHONE
    )

    with pytest.raises(DomainError):
        await dispatch(the_desk, booking_id)

    assert not [r for r in uow.state.requests.values() if r.booking_id == booking_id]
