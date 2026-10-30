"""Meeting and work item jobs (ADR 0017, 0018) on top of ``CallService``.

The jobs add what only the worker knows: whether a job is stale (the booking moved or was
cancelled), whether it has nothing to do (the result exists, or the group does not want one:
skipped with a reason, ADR 0068) and logging. Failures are classified by the runner
(``IntegrationError.retryable``).

    create_video_room  ─► CallService.ensure_meeting
    dispatch_wxcc_task ─► CallService.ensure_task   (waits for the meeting)
    delete_video_room  ─► CallService.delete_meeting
    delete_trial_call  ─► CallService.delete_trial_call   (ADR 0071)
    end_wxcc_task      ─► CallService.end_task      (cancelled bookings, ADR 0021)
"""

import structlog

from app.adapters.db.jobs import ClaimedJob
from app.domain.booking import Booking
from app.domain.call_service import CallService
from app.domain.calls import EventKind
from app.domain.job_plan import JobType, is_stale
from app.domain.ports import UnitOfWork
from app.domain.scheduling import Group

log = structlog.get_logger()

NO_BOOKING = "no_booking"
"""Skip reason: the booking or its group no longer exists."""
STALE = "stale"
"""Skip reason: the booking moved or is no longer confirmed since the job was planned."""
ALREADY_DONE = "already_done"
"""Skip reason: the meeting or work item exists already (a retry after a partial run)."""
NO_VIDEO = "no_video"
"""Skip reason: the group has no Webex host, so no meeting is made (ADR 0068)."""
OWN_QUEUE = "own_queue"
"""Skip reason: the group's agents pick bookings up in OpenPlanner (ADR 0043): no work item."""
NO_DISPATCH = "no_dispatch"
"""Skip reason: the group has no WxCC channel or business address: no work item."""

FAILURE_EVENTS = {
    JobType.CREATE_VIDEO_ROOM: EventKind.MEETING_FAILED,
    JobType.DISPATCH_WXCC_TASK: EventKind.TASK_FAILED,
}
"""Timeline event of a job's final failure; other call jobs record ``integration_error``."""

CALL_JOBS = frozenset(
    {
        JobType.CREATE_VIDEO_ROOM,
        JobType.DELETE_VIDEO_ROOM,
        JobType.DELETE_TRIAL_CALL,
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
            types |= {
                JobType.CREATE_VIDEO_ROOM,
                JobType.DELETE_VIDEO_ROOM,
                JobType.DELETE_TRIAL_CALL,
            }
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
        elif job.type is JobType.DELETE_TRIAL_CALL:
            await self._calls.delete_trial_call(booking, group)
        elif job.type is JobType.END_WXCC_TASK:
            await self._calls.end_task(booking)
        elif is_stale(job.pinned_start, booking):
            log.info("job.skipped", job_id=job.id, reason=STALE)
            return STALE
        elif (reason := nothing_to_do(job.type, booking, group)) is not None:
            log.info("job.skipped", job_id=job.id, reason=reason)
            return reason
        elif job.type is JobType.CREATE_VIDEO_ROOM:
            await self._calls.ensure_meeting(booking, group)
        else:
            await self._calls.ensure_task(booking, group)
        return None

    async def record_failure(self, job: ClaimedJob, code: str, message: str = "") -> None:
        """Put a job's final failure on the booking's timeline, with the provider's reason."""
        kind = FAILURE_EVENTS.get(job.type, EventKind.INTEGRATION_ERROR)
        await self._calls.record_failure(job.booking_id, code, kind, message)


def nothing_to_do(job_type: JobType, booking: Booking, group: Group) -> str | None:
    """Return why a meeting or work item job has nothing to do, or ``None`` when it has.

    A job that does nothing is recorded as skipped with this reason (ADR 0068), so the audit
    log never says "succeeded" about a work item that was never created.
    """
    if job_type is JobType.CREATE_VIDEO_ROOM:
        if booking.meeting is not None:
            return ALREADY_DONE
        return None if group.video_enabled else NO_VIDEO
    if booking.wxcc_task_id:
        return ALREADY_DONE
    if group.dispatch_enabled:
        return None
    return OWN_QUEUE if group.has_own_queue else NO_DISPATCH
