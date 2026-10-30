"""Planning a booking's meeting or work item job again from the booking page (ADR 0068).

Whoever holds ``bookings.manage`` on the group may plan ``create_video_room`` or
``dispatch_wxcc_task`` again for an active booking that still lacks the result. The pending
job of that type, if any, is replaced, so a click never doubles a job; the new job runs now
(the meeting) or at the dispatch moment (the work item), pinned to the booking's start as
every call job is. The retry is audited as a ``bookings.manage`` change with the job type;
the job's own outcome lands on the timeline when the worker runs it.

    retry(principal, slug, booking_id, type) ─► authorise ─► booking active, result missing
                                              ─► replace pending job ─► audit
"""

from datetime import datetime

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal
from app.domain.booking import Booking
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import RETRYABLE_JOBS, JobType, PlannedJob
from app.domain.ports import Clock, UnitOfWork
from app.domain.scheduling import Group


class CallRetryService:
    """Plans a failed call job again.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises and audits the retry.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def retry(self, principal: Principal, slug: str, booking_id: int, job: JobType) -> None:
        """Plan ``job`` again for the booking.

        Raises:
            DomainError: ``not_permitted`` without ``bookings.manage`` on the group;
                ``booking_not_found`` for a booking that is not the group's;
                ``booking_not_active`` for a cancelled or lapsed booking;
                ``nothing_to_retry`` for a job that is not retryable or whose result exists.
        """
        authorization = require(
            await self._admin.authorize(principal, Permission.BOOKINGS_MANAGE, group_slug=slug)
        )
        if job not in RETRYABLE_JOBS:
            raise DomainError(ErrorCode.NOTHING_TO_RETRY, job.value)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            booking = await tx.get_booking(booking_id)
            group = await tx.get_group(slug)
            if booking is None or group is None or booking.group_id != group.id:
                raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
            if not booking.is_active:
                raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
            if not _missing(booking, group, job):
                raise DomainError(ErrorCode.NOTHING_TO_RETRY, job.value)
            await tx.delete_pending_jobs(booking_id, {job})
            await tx.add_jobs(
                booking_id, [PlannedJob(job, _run_at(booking, group, job, now), booking.start)]
            )
            await record_change(
                tx,
                now,
                principal,
                Permission.BOOKINGS_MANAGE,
                authorization.target.organisation,
                slug,
                f"retry.{job.value}",
            )


def _missing(booking: Booking, group: Group, job: JobType) -> bool:
    """Whether the job's result is still missing while the group expects it."""
    if job is JobType.CREATE_VIDEO_ROOM:
        return group.video_enabled and booking.meeting is None
    return group.dispatch_enabled and not booking.wxcc_task_id


def _run_at(booking: Booking, group: Group, job: JobType, now: datetime) -> datetime:
    """When the job runs: now for the meeting; the dispatch moment, if still ahead, for the task."""
    if job is JobType.DISPATCH_WXCC_TASK:
        return max(now, booking.start - group.dispatch_lead)
    return now
