"""Meeting and work item jobs (ADR 0017, 0018) on top of ``CallService``.

The jobs add what only the worker knows: whether a job is stale (the booking moved or was
cancelled), whether it has nothing to do (the result exists, or the group does not want one:
skipped with a reason, ADR 0068) and logging. Failures are classified by the runner
(``IntegrationError.retryable``).

    create_video_room  ─► CallService.ensure_meeting
    dispatch_wxcc_task ─► CallService.ensure_task   (waits for the meeting; member
                          DISPATCH_CONTACT_CENTER_TASK, ADR 0032)
    delete_video_room  ─► CallService.delete_meeting
    delete_trial_call  ─► CallService.delete_trial_call   (ADR 0071)
    rename_guest       ─► CallService.rename_guest        (ADR 0123 point 7; added by the purge)
    end_wxcc_task      ─► CallService.end_task      (cancelled bookings, ADR 0021)

A dispatch job of request work (a new flow on a counted pool, ADR 0121 step 6b) goes to
``RequestDispatch`` instead: the flow, not the group, decides whether it reaches the contact
center, and an attempt in doubt is never retried.
"""

import structlog

from app.adapters.db.jobs import ClaimedJob
from app.domain.booking import Booking
from app.domain.call_service import CallService
from app.domain.calls import EventKind
from app.domain.fallback import FALLBACK
from app.domain.job_plan import JobType, is_stale
from app.domain.ports import UnitOfWork
from app.domain.request_dispatch import RequestDispatch
from app.domain.scheduling import Group
from app.domain.service_flows import FlowRevision

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
"""Skip reason: the group's contact-center routing is missing or incomplete: no task."""
NO_GUEST_KEY = "no_guest_key"
"""Skip reason: this worker has no video provider or no key to work out the guest's subject."""

FAILURE_EVENTS = {
    JobType.CREATE_VIDEO_ROOM: EventKind.MEETING_FAILED,
    JobType.DISPATCH_CONTACT_CENTER_TASK: EventKind.TASK_FAILED,
}
"""Timeline event of a job's final failure; other call jobs record ``integration_error``."""

CALL_JOBS = frozenset(
    {
        JobType.CREATE_VIDEO_ROOM,
        JobType.DELETE_VIDEO_ROOM,
        JobType.DELETE_TRIAL_CALL,
        JobType.DISPATCH_CONTACT_CENTER_TASK,
        JobType.END_WXCC_TASK,
        JobType.RENAME_GUEST,
    }
)


class CallJobs:
    """Runs the meeting and work item jobs.

    Args:
        uow: Reads bookings and groups.
        calls: Creates and removes meetings and work items.
        requests: Dispatches the request work of new flows (ADR 0121 step 6b), if any.
    """

    def __init__(
        self, uow: UnitOfWork, calls: CallService, requests: RequestDispatch | None = None
    ) -> None:
        """Store the collaborators; ``requests`` dispatches request work (ADR 0121 step 6b)."""
        self._uow = uow
        self._calls = calls
        self._requests = requests

    async def _request_revision(self, booking: Booking) -> FlowRevision | None:
        """Return the flow revision of ``booking`` when it is request work, else ``None``."""
        return await self._requests.handles(booking) if self._requests is not None else None

    async def _dispatch_request(
        self, job: ClaimedJob, booking: Booking, group: Group, revision: FlowRevision
    ) -> str | None:
        """Dispatch the request work of ``booking``; return why it was skipped, if it was."""
        reason = (
            ALREADY_DONE
            if booking.contact_center_task or self._requests is None
            else await self._requests.dispatch(booking, group, revision)
        )
        if reason is not None:
            log.info("job.skipped", job_id=job.id, reason=reason)
        return reason

    @property
    def types(self) -> set[JobType]:
        """The job types this worker can run with the configured adapters."""
        types: set[JobType] = set()
        if self._calls.video_available:
            types |= {
                JobType.CREATE_VIDEO_ROOM,
                JobType.DELETE_VIDEO_ROOM,
                JobType.DELETE_TRIAL_CALL,
                JobType.RENAME_GUEST,
            }
        if self._calls.dispatch_available:
            types.add(JobType.DISPATCH_CONTACT_CENTER_TASK)
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
            if job.pinned_start is not None and (
                booking.trial is None or booking.trial.expires_at != job.pinned_start
            ):
                # Bound to an older trial that was replaced: the newer one has its own cleanup.
                log.info("job.skipped", job_id=job.id, reason=STALE)
                return STALE
            await self._calls.delete_trial_call(booking, group)
        elif job.type is JobType.END_WXCC_TASK:
            await self._calls.end_task(booking, group)
        elif job.type is JobType.RENAME_GUEST:
            # ADR 0123 point 7: after the purge, so the booking has no contact and nothing to
            # compare; the subject comes from its id and the keys.
            if not await self._calls.rename_guest(booking):
                log.info("job.skipped", job_id=job.id, reason=NO_GUEST_KEY)
                return NO_GUEST_KEY
        elif is_stale(job.pinned_start, booking):
            log.info("job.skipped", job_id=job.id, reason=STALE)
            return STALE
        elif job.type is JobType.DISPATCH_CONTACT_CENTER_TASK and (
            revision := await self._request_revision(booking)
        ):
            # ADR 0121 step 6b: the flow decides where request work goes, not the group.
            return await self._dispatch_request(job, booking, group, revision)
        elif job.type is JobType.DISPATCH_CONTACT_CENTER_TASK and await self._calls.diverts(
            booking, group
        ):
            # ADR 0082 point 11: the contact center is down; the booking went to the own queue.
            log.info("job.skipped", job_id=job.id, reason=FALLBACK)
            return FALLBACK
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
        if job.type is JobType.DISPATCH_CONTACT_CENTER_TASK:
            await self._calls.dispatch_failed(job.booking_id)


def nothing_to_do(job_type: JobType, booking: Booking, group: Group) -> str | None:
    """Return why a meeting or work item job has nothing to do, or ``None`` when it has.

    A job that does nothing is recorded as skipped with this reason (ADR 0068), so the audit
    log never says "succeeded" about a work item that was never created.
    """
    if job_type is JobType.CREATE_VIDEO_ROOM:
        if booking.meeting is not None:
            return ALREADY_DONE
        return None if group.video_enabled else NO_VIDEO
    if booking.contact_center_task:
        return ALREADY_DONE
    if group.dispatch_enabled:
        return None
    return OWN_QUEUE if group.has_own_queue else NO_DISPATCH
