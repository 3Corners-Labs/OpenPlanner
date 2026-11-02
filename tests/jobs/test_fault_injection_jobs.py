"""A provider that times out leaves a job retried or failed with a code, never half done.

ASVS V16.3.3 (failures are logged as events) and V16.5.2 (a failing dependency is handled
without breaking the application). The provider is the real Webex adapter on a transport that
times out, so the whole chain runs: adapter code, job runner, system event, booking timeline.
"""

import httpx
import pytest

from app.adapters.webex_meetings import WebexMeetings
from app.domain.audit import AuditEvent, Category, Outcome
from app.domain.booking import BookingStatus
from app.domain.calls import CallState, EventKind
from app.domain.job_plan import JobType
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MessageComposer
from app.jobs.runner import BACKOFF, JobRunner
from tests.adapters.test_fault_injection_outbound import PII
from tests.fakes.messaging import RefusingSender
from tests.fakes.tokens import authorized_client
from tests.jobs.test_call_jobs import WIRED, Setup, _uow
from tests.jobs.test_runner import FakeQueue, job
from tests.support.domain import TEST_TOKEN_KEY


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.fixture
def setup() -> Setup:
    fixture = Setup(_uow(**WIRED))
    fixture.video = WebexMeetings(  # type: ignore[assignment]  # the real adapter, a dead network
        authorized_client(_timeout), "https://webexapis.com/v1", fixture.clock
    )
    return fixture


def _system_events(setup: Setup, action: str) -> list[AuditEvent]:
    return [e for e in setup.uow.state.audit if e.action == action]


def _assert_a_code_and_no_personal_data(event: AuditEvent) -> None:
    assert event.reason.isidentifier()  # a machine code, not a sentence
    assert not any(value in repr(event) for value in PII)


async def test_a_timeout_is_retried_with_a_code_and_the_booking_is_untouched(setup: Setup) -> None:
    bid = await setup.booking()
    queue = FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])

    await setup.runner(queue).run_due()

    assert [(retried[0], retried[2]) for retried in queue.retried] == [(1, "unreachable")]
    assert (queue.done, queue.failed) == ([], [])
    (event,) = _system_events(setup, "job.create_video_room")
    assert (event.outcome, event.reason, event.category) == (
        Outcome.RETRY,
        "unreachable",
        Category.SYSTEM,
    )
    _assert_a_code_and_no_personal_data(event)
    booking = setup.uow.state.bookings[bid]
    assert booking.meeting is None  # no half-created meeting is remembered
    assert booking.status is BookingStatus.CONFIRMED
    assert EventKind.MEETING_CREATED not in setup.events(bid)


async def test_the_last_timeout_fails_the_job_visibly_and_still_creates_no_meeting(
    setup: Setup,
) -> None:
    bid = await setup.booking()
    queue = FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM, attempts=len(BACKOFF) + 1)])

    await setup.runner(queue).run_due()

    assert queue.failed == [(1, "unreachable")]
    (event,) = _system_events(setup, "job.create_video_room")
    assert (event.outcome, event.reason) == (Outcome.FAILURE, "unreachable")
    _assert_a_code_and_no_personal_data(event)
    timeline = setup.uow.state.booking_events[bid]
    assert (timeline[-1].kind, timeline[-1].detail) == (EventKind.MEETING_FAILED, "unreachable")
    assert not any(value in repr(timeline) for value in PII)
    assert setup.uow.state.call_status[bid].call is CallState.FAILED
    assert setup.uow.state.bookings[bid].meeting is None


async def test_a_work_item_is_not_dispatched_for_a_meeting_that_was_never_made(
    setup: Setup,
) -> None:
    # Fail closed: the agent's task needs the join link; without it nothing is dispatched.
    bid = await setup.booking()
    await setup.runner(FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])).run_due()
    task = FakeQueue([job(bid, JobType.DISPATCH_WXCC_TASK)])

    await setup.runner(task).run_due()

    assert setup.dispatcher.items == []
    assert setup.uow.state.bookings[bid].wxcc_task_id is None


async def test_a_mail_provider_that_times_out_is_retried_with_a_code(setup: Setup) -> None:
    bid = await setup.booking()
    sender = RefusingSender("unreachable", retryable=True)
    queue = FakeQueue([job(bid, JobType.SEND_CONFIRMATION)])
    composer = MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://x")

    await JobRunner(queue, setup.uow, setup.clock, sender, composer).run_due()

    assert [(retried[0], retried[2]) for retried in queue.retried] == [(1, "unreachable")]
    (event,) = _system_events(setup, "job.send_confirmation")
    assert (event.outcome, event.reason) == (Outcome.RETRY, "unreachable")
    _assert_a_code_and_no_personal_data(event)
