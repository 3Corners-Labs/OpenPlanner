"""Direct calls create their meeting and work item under the job's claim (review finding 2)."""

import pytest

from app.domain.admin import AdminService
from app.domain.booking import MeetingRef
from app.domain.call_service import CallService
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType, PlannedJob
from app.domain.ports import IntegrationError, MeetingRequest
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, make_group, window

WIRED = {
    "video_host_email": "host@example.nl",
    "wxcc_business_address": "openplanner@example.biz",
    "wxcc_channel": "openplanner-video",
}


class OverlappingVideo(FakeVideo):
    """A provider that lets the worker try its claim while the meeting is being made."""

    def __init__(self, uow: InMemoryUnitOfWork, booking_id: int) -> None:
        super().__init__()
        self._uow, self._booking_id = uow, booking_id
        self.worker_got_the_job: bool | None = None

    async def create_meeting(self, request: MeetingRequest) -> MeetingRef:
        async with self._uow.locked() as tx:
            self.worker_got_the_job = await tx.claim_booking_job(
                self._booking_id, JobType.CREATE_VIDEO_ROOM, NOW
            )
        return await super().create_meeting(request)


async def _setup(video_factory=FakeVideo):  # type: ignore[no-untyped-def]
    state = State()
    state.groups[1] = make_group(id=1, slug="aangifte", name="Aangifte", **WIRED)
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    uow = InMemoryUnitOfWork(state)
    clock = FixedClock(NOW)
    receipt = await book(BookingService(uow, clock, TEST_TOKEN_KEY))
    booking_id = receipt.booking.id or 0
    state.jobs[booking_id] = [
        PlannedJob(JobType.CREATE_VIDEO_ROOM, NOW, None),
        PlannedJob(JobType.DISPATCH_WXCC_TASK, NOW, None),
    ]
    video = video_factory(uow, booking_id) if video_factory is not FakeVideo else FakeVideo()
    dispatcher = FakeDispatcher()
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    calls = CallService(uow, integration, video, dispatcher, lambda b, g: "label")
    return uow, calls, video, dispatcher, receipt.booking, booking_id


async def test_the_request_takes_both_jobs_and_makes_one_meeting_and_one_task() -> None:
    uow, calls, video, dispatcher, booking, booking_id = await _setup()

    await calls.create_now(booking, make_group(id=1, **WIRED), NOW)

    assert (len(video.created), len(dispatcher.items)) == (1, 1)
    left = {j.type for j in uow.state.jobs[booking_id]}
    assert not left & {JobType.CREATE_VIDEO_ROOM, JobType.DISPATCH_WXCC_TASK}


async def test_the_worker_cannot_claim_the_meeting_job_while_the_request_makes_it() -> None:
    uow, calls, video, _, booking, booking_id = await _setup(OverlappingVideo)

    await calls.create_now(booking, make_group(id=1, **WIRED), NOW)

    assert video.worker_got_the_job is False
    assert len(video.created) == 1


async def test_a_step_the_worker_already_holds_is_left_to_it() -> None:
    uow, calls, video, _, booking, booking_id = await _setup()
    async with uow.locked() as tx:
        assert await tx.claim_booking_job(booking_id, JobType.CREATE_VIDEO_ROOM, NOW)

    with pytest.raises(IntegrationError, match="meeting_missing"):  # the task waits for it
        await calls.create_now(booking, make_group(id=1, **WIRED), NOW)

    assert video.created == []


async def test_a_failed_step_puts_its_job_back_for_the_worker() -> None:
    uow, calls, video, _, booking, booking_id = await _setup()
    video.error = IntegrationError("server_error", retryable=True)

    with pytest.raises(IntegrationError):
        await calls.create_now(booking, make_group(id=1, **WIRED), NOW)

    assert JobType.CREATE_VIDEO_ROOM in [j.type for j in uow.state.jobs[booking_id]]
