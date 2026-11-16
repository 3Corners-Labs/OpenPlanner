"""Request work at the contact center, for the ADR 0121 step 6b tests.

The demo service of ``tests.support.request_work`` (a counted pool of one unit, an own queue)
with a Webex Contact Center routing and a third flow: no meeting (``none``), dispatched to the
contact center, completed by the platform's event. ``Desk`` wires the dispatch, the platform
events and the queue as the worker and the web process do, on a fake dispatcher.
"""

from dataclasses import dataclass, replace
from datetime import timedelta

from app.domain.admin import AdminService
from app.domain.call_service import CallService
from app.domain.calls import EventKind
from app.domain.contact_center import ContactCenterKind, TaskKind
from app.domain.flow_resolution import FlowRef
from app.domain.integration import EventOutcome, IntegrationService
from app.domain.job_plan import JobType
from app.domain.pool_booking import PoolBookings
from app.domain.ports import IntegrationError
from app.domain.queue_service import QueueService
from app.domain.request_dispatch import AttemptEvents, RequestDispatch
from app.domain.request_work import RequestWork
from app.domain.scheduling import DispatchKind as GroupDispatch
from app.domain.scheduling import JoinClient
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    HandlingAction,
    InteractionKind,
)
from app.domain.service_requests import ServiceRequest, WorkItem
from app.domain.webhook_replay import INSTALLATION_SCOPE
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo, webex_cc
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW, webex_routing
from tests.support.request_work import POOL, world
from tests.support.service_flows import revision

CC_FLOW = FlowRef(53, 1)
"""A flow without a meeting, dispatched to the contact center, completed by the platform."""
WINDOW = 10
"""``contact_center_reconcile_minutes`` of the tests."""
SOURCE = "webex_cc"
"""The platform's code under which its event keys are remembered."""


def cc_world() -> InMemoryUnitOfWork:
    """Return the demo store with a Webex routing on the service and the contact-center flow."""
    uow = world()
    state = uow.state
    state.groups[1] = replace(state.groups[1], contact_center=webex_routing())
    state.flow_revisions[(53, 1)] = revision(
        flow_id=53,
        capacity_pool_id=POOL,
        duration_minutes=30,
        interaction_kind=InteractionKind.NONE,
        handling_action=HandlingAction.VIEW_REQUEST,
        dispatch=DispatchKind.CONTACT_CENTER,
        completion=CompletionPolicy.PROVIDER_EVENT,
        published_at=NOW,
    )
    return uow


@dataclass
class Desk:
    """The dispatch, the events and the queue of one store, on one fake dispatcher."""

    uow: InMemoryUnitOfWork
    clock: FixedClock
    dispatcher: FakeDispatcher
    work: RequestWork
    integration: IntegrationService
    dispatch: RequestDispatch
    queue: QueueService
    requeued: list[int]
    """Bookings whose contact-center work went back to the own queue (the fallback's notice)."""
    calls: CallService
    """Runs the call jobs' use cases, the end-task job among them (ADR 0021)."""


def desk(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    own_connections: frozenset[int] = frozenset(),
    *,
    uses_call_page: bool = False,
) -> Desk:
    """Return the request dispatch of ``uow`` wired as the worker wires it.

    ``own_connections`` are the organisations with a contact-center connection of their own;
    ``uses_call_page`` is the answer of the call-page rule of ADR 0123 step 6b, which the worker
    builds with ``call_page_rule``.
    """
    work = RequestWork(
        PoolBookings(clock, treatment_buffer=5), lease_minutes=5, unresolved_hours=24
    )

    async def has_own(organisation_id: int) -> bool:
        return organisation_id in own_connections

    integration = IntegrationService(
        uow, clock, AdminService(uow, clock), work=AttemptEvents(work, lambda: WINDOW, has_own)
    )
    requeued: list[int] = []

    async def note(booking_id: int) -> None:
        requeued.append(booking_id)

    integration.set_requeue_handler(note)
    dispatcher = FakeDispatcher()
    calls = CallService(
        uow,
        integration,
        FakeVideo(),
        webex_cc(dispatcher),
        lambda b, g: "x",
        opening=lambda locale, task: f"opening {task.reference}",
        uses_page=lambda group: uses_call_page,
    )
    dispatch = RequestDispatch(
        uow,
        work,
        clock,
        task_of=calls.task_for,
        dispatcher_of=calls.dispatcher_of,
        integration=integration,
        window_minutes=lambda: WINDOW,
    )
    queue = QueueService(uow, clock, AdminService(uow, clock), b"k" * 32, work=work)
    return Desk(uow, clock, dispatcher, work, integration, dispatch, queue, requeued, calls)


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


async def event(
    the_desk: Desk,
    task_id: str,
    kind: EventKind,
    origin: str = "",
    task_kind: TaskKind | None = None,
) -> EventOutcome:
    """Deliver one platform event of ``task_id``; ``task_kind`` as the event names it."""
    return await the_desk.integration.record_task_event(
        task_id,
        kind,
        the_desk.clock.now(),
        origin=origin,
        task_kind=task_kind,
        provider=ContactCenterKind.WEBEX_CC,
        source=SOURCE,
        scope=INSTALLATION_SCOPE,
        body_identity="",
    )


AMBIGUOUS = IntegrationError("unreachable", retryable=True, ambiguous=True)
"""A create whose outcome is unknown: the platform may have made the task."""


def routed(
    uow: InMemoryUnitOfWork, kind: TaskKind, *, dispatch_kind: GroupDispatch | None = None
) -> None:
    """Route the demo service to tasks of ``kind``, on the call page (ADR 0123 step 6b).

    ``dispatch_kind`` ``contact_center`` makes the service dispatch its own (legacy) bookings.
    """
    group = uow.state.groups[1]
    uow.state.groups[1] = replace(
        group,
        contact_center=webex_routing(task_kind=kind),
        join_client=JoinClient.EMBEDDED,
        dispatch_kind=dispatch_kind or group.dispatch_kind,
    )


async def fenced(the_desk: Desk, booking_id: int) -> None:
    """Dispatch ``booking_id`` with an unknown outcome and fence it after the window."""
    the_desk.dispatcher.error = AMBIGUOUS
    await dispatch(the_desk, booking_id)
    the_desk.dispatcher.error = None
    the_desk.clock.advance(timedelta(minutes=WINDOW))
    assert await the_desk.dispatch.fence_round() == 1


async def ensure(the_desk: Desk, booking_id: int) -> None:
    """Run the dispatch job's use case for a booking without a request (ADR 0018)."""
    booking = the_desk.uow.state.bookings[booking_id]
    await the_desk.calls.ensure_task(booking, the_desk.uow.state.groups[booking.group_id])


async def end_jobs(the_desk: Desk, booking_id: int) -> None:
    """Run the end-task job of ``booking_id`` as the worker does (ADR 0021)."""
    assert any(j.type is JobType.END_WXCC_TASK for j in the_desk.uow.state.jobs[booking_id])
    booking = the_desk.uow.state.bookings[booking_id]
    await the_desk.calls.end_task(booking, the_desk.uow.state.groups[booking.group_id])
