"""One meeting per pool booking of a video flow, through the existing jobs (ADR 0121 row 36).

A booking of a new video flow on a counted pool gets its meeting from ``create_video_room`` as
any booking does: retried while the provider is away, its final failure on the timeline (ADR
0065), deleted on cancellation, and nothing done for a booking that moved since the job was
planned (``pinned_start``). A booking of a flow without video gets no meeting even when its
service has a video host (A19), and no join link in its message.
"""

from dataclasses import replace
from datetime import timedelta

from app.domain.admin import AdminService
from app.domain.call_service import CallService
from app.domain.calls import EventKind
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType
from app.domain.pool_booking import PoolBookings
from app.domain.ports import IntegrationError
from app.domain.request_work import RequestWork
from app.i18n import Translator, load_catalogues
from app.jobs.calls import FLOW_WITHOUT_VIDEO, CallJobs
from app.jobs.messages import MessageComposer
from app.jobs.runner import JobRunner
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeVideo
from tests.fakes.jobs import FakeQueue, job
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY
from tests.support.request_work import FLOW, VIDEO_FLOW, book, world

HOST = "host@voorbeeld.nl"


def hosted() -> InMemoryUnitOfWork:
    """Return the request-work world whose service has a video host."""
    uow = world()
    uow.state.groups[1] = replace(uow.state.groups[1], video_host_email=HOST)
    return uow


def runner_of(
    uow: InMemoryUnitOfWork, clock: FixedClock, video: FakeVideo, jobs: FakeQueue
) -> JobRunner:
    """Return a job runner with request work and the fake video provider."""
    work = RequestWork(
        PoolBookings(clock, treatment_buffer=5), lease_minutes=5, unresolved_hours=24
    )
    return JobRunner(
        jobs,
        uow,
        clock,
        None,
        MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://x"),
        video=video,
        integration=IntegrationService(uow, clock, AdminService(uow, clock)),
        request_work=work,
    )


async def test_a_video_flows_pool_booking_gets_one_meeting_and_a_flow_without_video_none() -> None:
    uow, clock, video = hosted(), FixedClock(NOW), FakeVideo()
    with_video = await book(uow, clock, VIDEO_FLOW)
    without = await book(uow, clock, FLOW)
    jobs = FakeQueue(
        [job(with_video, JobType.CREATE_VIDEO_ROOM), job(without, JobType.CREATE_VIDEO_ROOM)]
    )

    await runner_of(uow, clock, video, jobs).run_due()
    await runner_of(
        uow, clock, video, FakeQueue([job(with_video, JobType.CREATE_VIDEO_ROOM)])
    ).run_due()

    assert len(video.created) == 1  # once, also when the job runs again
    assert video.created[0].host_email == HOST
    assert uow.state.bookings[with_video].meeting is not None
    assert uow.state.bookings[without].meeting is None
    events = [e.kind for e in uow.state.booking_events[with_video]]
    assert EventKind.MEETING_CREATED in events


async def test_the_meeting_job_of_a_flow_without_video_is_skipped_with_its_reason() -> None:
    uow, clock = hosted(), FixedClock(NOW)
    without = await book(uow, clock, FLOW)
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    calls = CallJobs(uow, CallService(uow, integration, FakeVideo(), {}, lambda b, g: ""))

    reason = await calls.run(job(without, JobType.CREATE_VIDEO_ROOM))

    assert reason == FLOW_WITHOUT_VIDEO


async def test_a_provider_failure_is_retried_and_its_final_failure_is_on_the_timeline() -> None:
    uow, clock, video = hosted(), FixedClock(NOW), FakeVideo()
    booking_id = await book(uow, clock, VIDEO_FLOW)
    video.error = IntegrationError("server_error", retryable=True)
    first = FakeQueue([job(booking_id, JobType.CREATE_VIDEO_ROOM)])
    last = FakeQueue([job(booking_id, JobType.CREATE_VIDEO_ROOM, attempts=4)])

    await runner_of(uow, clock, video, first).run_due()
    await runner_of(uow, clock, video, last).run_due()

    assert first.retried[0][1] == NOW + timedelta(minutes=1)
    assert last.failed == [(1, "server_error")]
    events = uow.state.booking_events[booking_id]
    assert (events[-1].kind, events[-1].detail) == (EventKind.MEETING_FAILED, "server_error")


async def test_a_moved_booking_gets_no_meeting_and_a_cancelled_one_loses_it() -> None:
    uow, clock, video = hosted(), FixedClock(NOW), FakeVideo()
    booking_id = await book(uow, clock, VIDEO_FLOW)
    moved = job(booking_id, JobType.CREATE_VIDEO_ROOM, pinned=SLOT_0900 - timedelta(days=7))

    await runner_of(uow, clock, video, FakeQueue([moved])).run_due()
    assert video.created == []

    await runner_of(
        uow, clock, video, FakeQueue([job(booking_id, JobType.CREATE_VIDEO_ROOM)])
    ).run_due()
    delete = job(booking_id, JobType.DELETE_VIDEO_ROOM, pinned=None)
    await runner_of(uow, clock, video, FakeQueue([delete])).run_due()

    assert video.deleted == [("meeting-1", HOST)]


async def test_only_a_video_flows_booking_gets_the_join_link_in_its_message() -> None:
    uow, clock = hosted(), FixedClock(NOW)
    composer = MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://x")
    group = uow.state.groups[1]
    links = {}
    for flow in (VIDEO_FLOW, FLOW):
        booking_id = await book(uow, clock, flow)
        booking = uow.state.bookings[booking_id]
        message = composer.compose(
            JobType.SEND_CONFIRMATION, booking, group, NOW, video=flow is VIDEO_FLOW
        )
        links[flow] = "/join" in message.text

    assert links == {VIDEO_FLOW: True, FLOW: False}
