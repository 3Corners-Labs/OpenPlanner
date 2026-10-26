"""Meeting and work item jobs (ADR 0017, 0018): idempotent, recorded, failures classified."""

from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.calls import CallState, EventKind, TaskState
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType
from app.domain.ports import IntegrationError
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MessageComposer
from app.jobs.runner import JobRunner
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.jobs.test_runner import FakeQueue, job
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, book, make_group, window

WIRED = {
    "video_host_email": "host@example.nl",
    "wxcc_business_address": "openplanner@example.biz",
    "wxcc_channel": "openplanner-video",
}


def _uow(**group: object) -> InMemoryUnitOfWork:
    state = State()
    state.groups[1] = make_group(id=1, slug="aangifte", name="Aangifte", **group)
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    return InMemoryUnitOfWork(state)


class Setup:
    def __init__(self, uow: InMemoryUnitOfWork) -> None:
        self.uow = uow
        self.clock = FixedClock(NOW)
        self.video = FakeVideo()
        self.dispatcher = FakeDispatcher()
        self.integration = IntegrationService(uow, self.clock, AdminService(uow, self.clock))

    def runner(self, queue: FakeQueue) -> JobRunner:
        composer = MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://x")
        return JobRunner(
            queue,
            self.uow,
            self.clock,
            None,
            composer,
            video=self.video,
            dispatcher=self.dispatcher,
            integration=self.integration,
        )

    async def booking(self) -> int:
        receipt = await book(BookingService(self.uow, self.clock, TEST_TOKEN_KEY))
        return receipt.booking.id or 0

    def events(self, booking_id: int) -> list[EventKind]:
        return [e.kind for e in self.uow.state.booking_events.get(booking_id, [])]


@pytest.fixture
def setup() -> Setup:
    return Setup(_uow(**WIRED))


async def test_meeting_then_work_item(setup: Setup) -> None:
    bid = await setup.booking()

    await setup.runner(FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])).run_due()
    await setup.runner(FakeQueue([job(bid, JobType.DISPATCH_WXCC_TASK)])).run_due()

    booking = setup.uow.state.bookings[bid]
    assert booking.meeting is not None
    assert booking.meeting.id == "meeting-1"
    assert booking.wxcc_task_id == "task-1"
    assert setup.video.created[0].host_email == "host@example.nl"
    item = setup.dispatcher.items[0]
    assert (item.first_name, item.meeting_link) == ("Ada", "https://site.webex.com/m/1")
    assert item.start_label == "5 jan 2026, 09:00"  # agent's view, group time zone
    assert setup.events(bid) == [EventKind.MEETING_CREATED, EventKind.TASK_CREATED]
    assert setup.uow.state.call_status[bid].task is TaskState.QUEUED


async def test_jobs_are_idempotent(setup: Setup) -> None:
    bid = await setup.booking()
    for _ in range(2):
        await setup.runner(FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])).run_due()
        await setup.runner(FakeQueue([job(bid, JobType.DISPATCH_WXCC_TASK)])).run_due()

    assert len(setup.video.created) == 1
    assert len(setup.dispatcher.items) == 1


async def test_cancellation_deletes_the_meeting(setup: Setup) -> None:
    bid = await setup.booking()
    await setup.runner(FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])).run_due()

    await setup.runner(FakeQueue([job(bid, JobType.DELETE_VIDEO_ROOM, pinned=None)])).run_due()

    assert setup.video.deleted == [("meeting-1", "host@example.nl")]
    assert setup.events(bid)[-1] is EventKind.MEETING_DELETED


async def test_groups_without_integrations_are_skipped() -> None:
    setup = Setup(_uow())
    bid = await setup.booking()
    queue = FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])

    await setup.runner(queue).run_due()
    await setup.runner(FakeQueue([job(bid, JobType.DISPATCH_WXCC_TASK)])).run_due()

    assert queue.done == [1]
    assert setup.video.created == []
    assert setup.dispatcher.items == []


async def test_dispatch_waits_for_the_meeting(setup: Setup) -> None:
    bid = await setup.booking()
    queue = FakeQueue([job(bid, JobType.DISPATCH_WXCC_TASK)])

    await setup.runner(queue).run_due()

    assert [error for _, _, error in queue.retried] == ["meeting_missing"]
    assert setup.dispatcher.items == []


async def test_refusals_fail_at_once_and_show_on_the_timeline(setup: Setup) -> None:
    bid = await setup.booking()
    await setup.runner(FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])).run_due()
    setup.dispatcher.error = IntegrationError("rejected_400", retryable=False)
    queue = FakeQueue([job(bid, JobType.DISPATCH_WXCC_TASK)])

    await setup.runner(queue).run_due()

    assert queue.failed == [(1, "rejected_400")]
    assert queue.retried == []
    events = setup.uow.state.booking_events[bid]
    assert (events[-1].kind, events[-1].detail) == (EventKind.TASK_FAILED, "rejected_400")


async def test_transient_failures_are_retried(setup: Setup) -> None:
    bid = await setup.booking()
    setup.video.error = IntegrationError("server_error", retryable=True)
    queue = FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])

    await setup.runner(queue).run_due()

    assert queue.retried[0][1] == NOW + timedelta(minutes=1)
    assert queue.failed == []


async def test_final_failure_is_recorded(setup: Setup) -> None:
    bid = await setup.booking()
    setup.video.error = IntegrationError("server_error", retryable=True)
    queue = FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM, attempts=4)])

    await setup.runner(queue).run_due()

    assert queue.failed == [(1, "server_error")]
    events = setup.uow.state.booking_events[bid]
    assert (events[-1].kind, events[-1].detail) == (EventKind.MEETING_FAILED, "server_error")
    assert setup.uow.state.call_status[bid].call is CallState.FAILED


async def test_a_refused_meeting_fails_at_once_as_meeting_failed(setup: Setup) -> None:
    bid = await setup.booking()
    setup.video.error = IntegrationError("rejected_400", retryable=False)

    await setup.runner(FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])).run_due()

    events = setup.uow.state.booking_events[bid]
    assert (events[-1].kind, events[-1].detail) == (EventKind.MEETING_FAILED, "rejected_400")


async def test_stale_dispatch_does_nothing(setup: Setup) -> None:
    bid = await setup.booking()
    await setup.runner(FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])).run_due()
    moved = SLOT_0900 + timedelta(hours=1)

    await setup.runner(FakeQueue([job(bid, JobType.DISPATCH_WXCC_TASK, pinned=moved)])).run_due()

    assert setup.dispatcher.items == []


async def test_only_configured_integrations_are_claimed(setup: Setup) -> None:
    composer = MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://x")
    queue = FakeQueue([])
    runner = JobRunner(
        queue,
        setup.uow,
        setup.clock,
        None,
        composer,
        video=setup.video,
        integration=setup.integration,
    )
    await runner.run_due()

    assert JobType.CREATE_VIDEO_ROOM in queue.requested_types
    assert JobType.DISPATCH_WXCC_TASK not in queue.requested_types


async def _dispatched(setup: Setup) -> int:
    bid = await setup.booking()
    await setup.runner(FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])).run_due()
    await setup.runner(FakeQueue([job(bid, JobType.DISPATCH_WXCC_TASK)])).run_due()
    return bid


async def test_a_cancelled_work_item_is_marked_by_v2_then_ended(setup: Setup) -> None:
    bid = await _dispatched(setup)

    await setup.runner(FakeQueue([job(bid, JobType.END_WXCC_TASK, pinned=None)])).run_due()

    reference = setup.uow.state.bookings[bid].reference
    assert setup.dispatcher.marked == [("task-1", reference)]
    assert setup.dispatcher.ended == ["task-1"]
    assert setup.events(bid)[-2:] == [EventKind.TASK_MARKED_CANCELLED, EventKind.TASK_END_REQUESTED]
    assert setup.uow.state.call_status[bid].task is TaskState.CANCELLED


@pytest.mark.parametrize(
    ("mark_error", "first"),
    [
        (None, (EventKind.TASK_MARKED_CANCELLED, "")),  # the v2 mark stands
        ("rejected_400", (EventKind.INTEGRATION_ERROR, "rejected_400")),  # v1 is still tried
    ],
)
async def test_a_refused_v1_end_is_recorded_after_the_v2_step(
    setup: Setup, mark_error: str | None, first: tuple[EventKind, str]
) -> None:
    bid = await _dispatched(setup)
    if mark_error:
        setup.dispatcher.mark_error = IntegrationError(mark_error, retryable=False)
    setup.dispatcher.end_error = IntegrationError("rejected_403", retryable=False)
    queue = FakeQueue([job(bid, JobType.END_WXCC_TASK, pinned=None)])

    await setup.runner(queue).run_due()

    assert queue.done == [1]
    events = setup.uow.state.booking_events[bid][-2:]
    assert [(e.kind, e.detail) for e in events] == [
        first,
        (EventKind.END_NOT_POSSIBLE, "rejected_403"),
    ]


async def test_a_retry_after_the_mark_does_not_mark_twice(setup: Setup) -> None:
    bid = await _dispatched(setup)
    setup.dispatcher.end_error = IntegrationError("server_error", retryable=True)
    end = job(bid, JobType.END_WXCC_TASK, pinned=None)
    queue = FakeQueue([end])

    await setup.runner(queue).run_due()
    setup.dispatcher.end_error = None
    await setup.runner(FakeQueue([end])).run_due()

    assert [error for _, _, error in queue.retried] == ["server_error"]
    assert len(setup.dispatcher.marked) == 1
    assert setup.dispatcher.ended == ["task-1"]
    assert EventKind.END_NOT_POSSIBLE not in setup.events(bid)


async def test_a_retryable_v2_failure_is_retried_before_v1(setup: Setup) -> None:
    bid = await _dispatched(setup)
    setup.dispatcher.mark_error = IntegrationError("server_error", retryable=True)
    queue = FakeQueue([job(bid, JobType.END_WXCC_TASK, pinned=None, attempts=4)])

    await setup.runner(queue).run_due()

    assert setup.dispatcher.ended == []
    events = setup.uow.state.booking_events[bid]
    assert (events[-1].kind, events[-1].detail) == (EventKind.INTEGRATION_ERROR, "server_error")
    assert setup.uow.state.call_status[bid].task is TaskState.QUEUED


async def test_nothing_to_end_without_a_task_or_after_it_ended(setup: Setup) -> None:
    bid = await setup.booking()
    end = job(bid, JobType.END_WXCC_TASK, pinned=None)
    await setup.runner(FakeQueue([end])).run_due()  # no task yet
    await setup.runner(FakeQueue([job(bid, JobType.CREATE_VIDEO_ROOM)])).run_due()
    await setup.runner(FakeQueue([job(bid, JobType.DISPATCH_WXCC_TASK)])).run_due()
    await setup.integration.record(bid, EventKind.TASK_ENDED)

    await setup.runner(FakeQueue([end])).run_due()  # the agent already ended it

    assert setup.dispatcher.ended == []
    assert setup.dispatcher.marked == []
