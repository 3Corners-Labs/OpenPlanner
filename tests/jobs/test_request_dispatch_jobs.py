"""The dispatch job of request work, wired as the worker wires it (ADR 0121 step 6b).

The flow decides where request work goes, not the service: a booking of a contact-center flow is
sent as an attempt even though the service itself uses the own queue, a booking of an own-queue
flow is never sent, and the runner exposes the fence round the worker calls with the watchdog.
"""

from app.domain.admin import AdminService
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType
from app.domain.pool_booking import PoolBookings
from app.domain.request_dispatch import AttemptEvents
from app.domain.request_work import RequestWork
from app.domain.service_flows import InteractionKind
from app.domain.service_requests import WorkChannel, WorkItemState
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MessageComposer
from app.jobs.runner import JobRunner
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo, webex_cc
from tests.fakes.jobs import FakeQueue, job
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.contact_center_requests import CC_FLOW, cc_world
from tests.support.domain import NOW, TEST_TOKEN_KEY
from tests.support.request_work import FLOW, book


def runner_of(
    uow: InMemoryUnitOfWork, clock: FixedClock, dispatcher: FakeDispatcher, jobs: FakeQueue
) -> JobRunner:
    """Return a job runner with request work, on the fake dispatcher."""
    work = RequestWork(
        PoolBookings(clock, treatment_buffer=5), lease_minutes=5, unresolved_hours=24
    )
    return JobRunner(
        jobs,
        uow,
        clock,
        None,
        MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://x"),
        video=FakeVideo(),
        dispatchers=webex_cc(dispatcher),
        integration=IntegrationService(
            uow, clock, AdminService(uow, clock), work=AttemptEvents(work, lambda: 10)
        ),
        request_work=work,
        reconcile_minutes=lambda: 10,
    )


async def test_the_flow_decides_where_request_work_goes() -> None:
    uow, clock, dispatcher = cc_world(), FixedClock(NOW), FakeDispatcher()
    routed = await book(uow, clock, CC_FLOW)
    own = await book(uow, clock, FLOW)
    jobs = FakeQueue(
        [
            job(routed, JobType.DISPATCH_CONTACT_CENTER_TASK),
            job(own, JobType.DISPATCH_CONTACT_CENTER_TASK),
        ]
    )
    runner = runner_of(uow, clock, dispatcher, jobs)

    await runner.run_due()

    [task] = dispatcher.items
    assert task.interaction is InteractionKind.NONE
    assert uow.state.bookings[routed].contact_center_task is not None
    assert uow.state.bookings[own].contact_center_task is None
    [item] = list(uow.state.work_items.values())
    assert (item.channel, item.state) == (WorkChannel.CONTACT_CENTER, WorkItemState.DISPATCHED)
    assert runner.requests is not None
    assert await runner.requests.fence_round() == 0
