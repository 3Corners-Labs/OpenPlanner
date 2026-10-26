"""Meeting and work item jobs (ADR 0017, 0018) on top of ``CallService``.

The jobs add what only the worker knows: whether a job is stale (the booking moved or was
cancelled) and logging. Failures are classified by the runner (``IntegrationError.retryable``).

    create_video_room  ─► CallService.ensure_meeting
    dispatch_wxcc_task ─► CallService.ensure_task   (waits for the meeting)
    delete_video_room  ─► CallService.delete_meeting
    end_wxcc_task      ─► CallService.end_task      (cancelled bookings, ADR 0021)
"""

import structlog

from app.adapters.db.jobs import ClaimedJob
from app.domain.call_service import CallService
from app.domain.job_plan import JobType, is_stale
from app.domain.ports import UnitOfWork

log = structlog.get_logger()

NO_BOOKING = "no_booking"
"""Skip reason: the booking or its group no longer exists."""
STALE = "stale"
"""Skip reason: the booking moved or is no longer confirmed since the job was planned."""

CALL_JOBS = frozenset(
    {
        JobType.CREATE_VIDEO_ROOM,
        JobType.DELETE_VIDEO_ROOM,
        JobType.DISPATCH_WXCC_TASK,
        JobType.END_WXCC_TASK,
    }
)


class CallJobs:
    """Runs the meeting and work item jobs.

    Args:
        uow: Reads bookings and groups.
        calls: Creates and removes meetings and work items.
    """

    def __init__(self, uow: UnitOfWork, calls: CallService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._calls = calls

    @property
    def types(self) -> set[JobType]:
        """The job types this worker can run with the configured adapters."""
        types: set[JobType] = set()
        if self._calls.video_available:
            types |= {JobType.CREATE_VIDEO_ROOM, JobType.DELETE_VIDEO_ROOM}
        if self._calls.dispatch_available:
            types.add(JobType.DISPATCH_WXCC_TASK)
        if self._calls.end_available:
            types.add(JobType.END_WXCC_TASK)
        return types

    async def run(self, job: ClaimedJob) -> str | None:
        """Execute one call job; return why it was skipped, or ``None`` when it was done."""
        async with self._uow.read() as tx:
            booking = await tx.get_booking(job.booking_id)
            group = await tx.get_group_by_id(booking.group_id) if booking else None
        if booking is None or booking.id is None or group is None:
            log.info("job.skipped", job_id=job.id, reason=NO_BOOKING)
            return NO_BOOKING
        if job.type is JobType.DELETE_VIDEO_ROOM:
            await self._calls.delete_meeting(booking, group)
        elif job.type is JobType.END_WXCC_TASK:
            await self._calls.end_task(booking)
        elif is_stale(job.pinned_start, booking):
            log.info("job.skipped", job_id=job.id, reason=STALE)
            return STALE
        elif job.type is JobType.CREATE_VIDEO_ROOM:
            await self._calls.ensure_meeting(booking, group)
        else:
            await self._calls.ensure_task(booking, group)
        return None

    async def record_failure(self, job: ClaimedJob, code: str) -> None:
        """Put a job's final failure on the booking's timeline."""
        dispatch = job.type is JobType.DISPATCH_WXCC_TASK
        await self._calls.record_failure(job.booking_id, code, dispatch=dispatch)
