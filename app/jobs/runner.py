"""Run due jobs: message jobs through a ``MessageSender``, and the PII purge.

Handled now: confirmation, change, reminder (email), cancellation, purge. Video rooms (WP5)
and WxCC dispatch (WP6) are not claimed until their adapters exist; their jobs wait.

    claim due ─► stale? (pinned start changed / not confirmed / purged) ─► done, nothing sent
             ─► purge ─► NULL PII
             ─► message ─► compose ─► send ─► done
             failure ─► retry after 1, 5, 15 min ─► failed
"""

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Protocol

import structlog

from app.adapters.db.jobs import ClaimedJob
from app.domain.channels import ChannelKind
from app.domain.job_plan import JobType, is_stale
from app.domain.ports import Clock, MessageSender, UnitOfWork
from app.jobs.messages import MESSAGE_KINDS, MessageComposer

log = structlog.get_logger()

BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
"""Delay before attempt 2, 3 and 4; after that the job fails."""


class JobQueue(Protocol):
    """The subset of ``SqlJobQueue`` the runner uses (a fake in tests)."""

    async def claim_due(
        self,
        now: datetime,
        types: Collection[JobType],
        channels: Collection[ChannelKind],
        limit: int = 20,
    ) -> list[ClaimedJob]:
        """Claim due jobs."""
        ...

    async def complete(self, job_id: int) -> None:
        """Mark a job done."""
        ...

    async def retry(self, job_id: int, run_at: datetime, error: str) -> None:
        """Schedule another attempt."""
        ...

    async def fail(self, job_id: int, error: str) -> None:
        """Give up on a job."""
        ...


class JobRunner:
    """Claims and executes due jobs.

    Args:
        queue: The job queue.
        uow: Reads bookings and groups; purges personal data.
        clock: Current time.
        sender: Email sender, or ``None`` when no SMTP server is configured.
        composer: Builds messages.
    """

    def __init__(
        self,
        queue: JobQueue,
        uow: UnitOfWork,
        clock: Clock,
        sender: MessageSender | None,
        composer: MessageComposer,
    ) -> None:
        """Store the collaborators and decide which job types this worker can handle."""
        self._queue = queue
        self._uow = uow
        self._clock = clock
        self._sender = sender
        self._composer = composer
        self._types: set[JobType] = {JobType.PURGE_PII}
        if sender is not None:
            self._types |= set(MESSAGE_KINDS)

    async def run_due(self) -> int:
        """Run every due job once; return how many were claimed."""
        now = self._clock.now()
        jobs = await self._queue.claim_due(now, self._types, {ChannelKind.EMAIL})
        for job in jobs:
            try:
                await self._run(job, now)
            except Exception as exc:  # any failure is retried; the type is logged, not the text
                await self._handle_failure(job, now, type(exc).__name__)
            else:
                await self._queue.complete(job.id)
        return len(jobs)

    async def _run(self, job: ClaimedJob, now: datetime) -> None:
        """Execute one job."""
        if job.type is JobType.PURGE_PII:
            async with self._uow.locked() as tx:
                await tx.purge_personal_data(job.booking_id, now)
            log.info("job.purged", job_id=job.id, booking_id=job.booking_id)
            return
        async with self._uow.read() as tx:
            booking = await tx.get_booking(job.booking_id)
            group = await tx.get_group_by_id(booking.group_id) if booking else None
        if booking is None or group is None or booking.contact is None:
            log.info("job.skipped", job_id=job.id, reason="no_recipient")
            return
        if is_stale(job.pinned_start, booking):
            log.info("job.skipped", job_id=job.id, reason="stale")
            return
        if self._sender is None:  # pragma: no cover - message jobs are not claimed then
            raise RuntimeError("no sender configured")
        await self._sender.send(self._composer.compose(job.type, booking, group, now))
        log.info("job.sent", job_id=job.id, type=job.type.value)

    async def _handle_failure(self, job: ClaimedJob, now: datetime, error: str) -> None:
        """Retry with backoff, or fail after the last attempt."""
        if job.attempts <= len(BACKOFF):
            await self._queue.retry(job.id, now + BACKOFF[job.attempts - 1], error)
            log.warning("job.retry", job_id=job.id, attempt=job.attempts, error=error)
        else:
            await self._queue.fail(job.id, error)
            log.error("job.failed", job_id=job.id, type=job.type.value, error=error)
