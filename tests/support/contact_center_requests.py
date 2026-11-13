"""Request work at the contact center, for the ADR 0121 step 6b tests.

The demo service of ``tests.support.request_work`` (a counted pool of one unit, an own queue)
with a Webex Contact Center routing and a third flow: no meeting (``none``), dispatched to the
contact center, completed by the platform's event. ``Desk`` wires the dispatch, the platform
events and the queue as the worker and the web process do, on a fake dispatcher.
"""

from dataclasses import dataclass, replace

from app.domain.admin import AdminService
from app.domain.call_service import CallService
from app.domain.flow_resolution import FlowRef
from app.domain.integration import IntegrationService
from app.domain.pool_booking import PoolBookings
from app.domain.queue_service import QueueService
from app.domain.request_dispatch import AttemptEvents, RequestDispatch
from app.domain.request_work import RequestWork
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    HandlingAction,
    InteractionKind,
)
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


def desk(uow: InMemoryUnitOfWork, clock: FixedClock) -> Desk:
    """Return the request dispatch of ``uow`` wired as the worker wires it."""
    work = RequestWork(
        PoolBookings(clock, treatment_buffer=5), lease_minutes=5, unresolved_hours=24
    )
    integration = IntegrationService(
        uow, clock, AdminService(uow, clock), work=AttemptEvents(work, lambda: WINDOW)
    )
    dispatcher = FakeDispatcher()
    calls = CallService(uow, integration, FakeVideo(), webex_cc(dispatcher), lambda b, g: "x")
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
    return Desk(uow, clock, dispatcher, work, integration, dispatch, queue)
