"""Meeting and work item jobs (ADR 0017, 0018): create and delete meetings, dispatch to WxCC.

Each job is idempotent: a booking that already has its meeting or task is left alone. A group
without the integration is skipped. What happened is recorded on the booking's timeline;
failures are classified by the runner (``IntegrationError.retryable``).

    create_video_room ─► meeting_request ─► VideoProvider.create_meeting ─► save, record
    dispatch_wxcc_task ─► meeting there? ─► work_item ─► Dispatcher.create_task ─► save, record
    delete_video_room ─► VideoProvider.delete_meeting ─► record
"""

from zoneinfo import ZoneInfo

import structlog

from app.adapters.db.jobs import ClaimedJob
from app.domain.booking import Booking
from app.domain.calls import EventKind, meeting_request, work_item
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType, is_stale
from app.domain.ports import ContactCenterDispatcher, IntegrationError, UnitOfWork, VideoProvider
from app.domain.scheduling import Group
from app.i18n.dates import format_clock, format_day_compact, local_day

log = structlog.get_logger()

AGENT_LOCALE = "nl"
"""Language of the start time shown to agents on Agent Desktop."""

CALL_JOBS = frozenset(
    {JobType.CREATE_VIDEO_ROOM, JobType.DELETE_VIDEO_ROOM, JobType.DISPATCH_WXCC_TASK}
)


def start_label(booking: Booking, group: Group) -> str:
    """Return the start as an agent reads it, in the group's time zone (``5 jan 2026, 09:00``)."""
    tz = ZoneInfo(group.timezone)
    day = format_day_compact(local_day(booking.start, tz), AGENT_LOCALE)
    return f"{day}, {format_clock(booking.start, tz, AGENT_LOCALE)}"


class CallJobs:
    """Runs the meeting and work item jobs.

    Args:
        uow: Reads bookings and groups; stores meeting and task ids.
        integration: Records events on the booking's timeline.
        video: Video provider, or ``None`` when Webex is not configured.
        dispatcher: WxCC dispatcher, or ``None`` when WxCC is not configured.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        integration: IntegrationService,
        video: VideoProvider | None,
        dispatcher: ContactCenterDispatcher | None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._integration = integration
        self._video = video
        self._dispatcher = dispatcher

    @property
    def types(self) -> set[JobType]:
        """The job types this worker can run with the configured adapters."""
        types: set[JobType] = set()
        if self._video is not None:
            types |= {JobType.CREATE_VIDEO_ROOM, JobType.DELETE_VIDEO_ROOM}
        if self._video is not None and self._dispatcher is not None:
            types.add(JobType.DISPATCH_WXCC_TASK)
        return types

    async def run(self, job: ClaimedJob) -> None:
        """Execute one call job."""
        async with self._uow.read() as tx:
            booking = await tx.get_booking(job.booking_id)
            group = await tx.get_group_by_id(booking.group_id) if booking else None
        if booking is None or booking.id is None or group is None:
            log.info("job.skipped", job_id=job.id, reason="no_booking")
            return
        if job.type is JobType.DELETE_VIDEO_ROOM:
            await self._delete(booking, group)
        elif is_stale(job.pinned_start, booking):
            log.info("job.skipped", job_id=job.id, reason="stale")
        elif job.type is JobType.CREATE_VIDEO_ROOM:
            await self._create(booking, group)
        else:
            await self._dispatch(booking, group)

    async def record_failure(self, job: ClaimedJob, code: str) -> None:
        """Put a job's final failure on the booking's timeline."""
        kind = (
            EventKind.TASK_FAILED
            if job.type is JobType.DISPATCH_WXCC_TASK
            else EventKind.INTEGRATION_ERROR
        )
        await self._integration.record(job.booking_id, kind, detail=code)

    async def _create(self, booking: Booking, group: Group) -> None:
        """Create the booking's meeting, unless the group has no video or it exists."""
        if self._video is None or not group.video_enabled or booking.meeting is not None:
            return
        meeting = await self._video.create_meeting(meeting_request(booking, group))
        async with self._uow.locked() as tx:
            await tx.save_meeting(_id(booking), meeting)
        await self._integration.record(_id(booking), EventKind.MEETING_CREATED)
        log.info("call.meeting_created", booking_id=booking.id)

    async def _dispatch(self, booking: Booking, group: Group) -> None:
        """Create the booking's work item, unless WxCC is off for the group or it exists."""
        if self._dispatcher is None or not group.dispatch_enabled or booking.wxcc_task_id:
            return
        if booking.meeting is None:
            raise IntegrationError("meeting_missing", retryable=True)
        task_id = await self._dispatcher.create_task(
            work_item(booking, group, start_label=start_label(booking, group))
        )
        async with self._uow.locked() as tx:
            await tx.save_task_id(_id(booking), task_id)
        await self._integration.record(_id(booking), EventKind.TASK_CREATED)
        log.info("call.task_created", booking_id=booking.id)

    async def _delete(self, booking: Booking, group: Group) -> None:
        """Delete the booking's meeting, if it has one."""
        if self._video is None or booking.meeting is None or group.video_host_email is None:
            return
        await self._video.delete_meeting(booking.meeting.id, group.video_host_email)
        await self._integration.record(_id(booking), EventKind.MEETING_DELETED)


def _id(booking: Booking) -> int:
    """Return the id of a stored booking."""
    if booking.id is None:  # pragma: no cover - stored bookings always have an id
        raise ValueError("booking has no id")
    return booking.id
