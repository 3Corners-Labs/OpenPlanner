"""Run due jobs: message jobs through a ``MessageSender``, and the PII purge.

Handled: confirmation, change, reminder (email and SMS), cancellation, the emergency closure
messages (suspension, update, reinstatement; ADR 0023), purge, and, when Webex and WxCC are
configured, meetings and work items (``app.jobs.calls``). Jobs of an unconfigured
integration are not claimed; they wait. A message for an organisation without a sender on
its channel fails at once as ``channel_not_configured`` (ADR 0022).

    claim due ─► stale? (pinned start changed / not confirmed / purged) ─► done, nothing sent
             ─► purge ─► NULL PII
             ─► message ─► sender of the group's organisation and the job's channel
                       ─► compose (group texts first) ─► send ─► done
             ─► meeting / work item ─► app.jobs.calls
             failure ─► retry after 1, 5, 15 min ─► failed (refusals fail at once)
"""

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Protocol

import structlog

from app.adapters.db.jobs import ClaimedJob
from app.domain.call_service import CallService
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType, is_stale
from app.domain.ports import (
    Clock,
    ContactCenterDispatcher,
    IntegrationError,
    MessageSender,
    UnitOfWork,
    VideoProvider,
)
from app.jobs.calls import CALL_JOBS, CallJobs
from app.jobs.messages import MESSAGE_KINDS, MessageComposer
from app.web.call_labels import agent_start_label

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


class SenderDirectory(Protocol):
    """Finds the sender of an organisation's channel (``ConfiguredSenders``; a fake in tests)."""

    async def sender(self, organisation_id: int, channel: ChannelKind) -> MessageSender | None:
        """Return the sender, or ``None`` when the organisation has none on ``channel``."""
        ...


MESSAGE_CHANNELS = frozenset({ChannelKind.EMAIL, ChannelKind.SMS})
"""Channels whose message jobs are claimed when a sender directory is available."""


class JobRunner:
    """Claims and executes due jobs.

    Args:
        queue: The job queue.
        uow: Reads bookings and groups; purges personal data.
        clock: Current time.
        senders: Finds the sender per organisation and channel; ``None`` claims no messages.
        composer: Builds messages.
        video: Webex meetings, or ``None`` when not configured.
        dispatcher: WxCC work items, or ``None`` when not configured.
        integration: Records call events; required when ``video`` is set.
    """

    def __init__(
        self,
        queue: JobQueue,
        uow: UnitOfWork,
        clock: Clock,
        senders: SenderDirectory | None,
        composer: MessageComposer,
        *,
        video: VideoProvider | None = None,
        dispatcher: ContactCenterDispatcher | None = None,
        integration: IntegrationService | None = None,
    ) -> None:
        """Store the collaborators and decide which job types this worker can handle."""
        self._queue = queue
        self._uow = uow
        self._clock = clock
        self._senders = senders
        self._composer = composer
        self._types: set[JobType] = {JobType.PURGE_PII}
        if senders is not None:
            self._types |= set(MESSAGE_KINDS)
        self._calls = (
            CallJobs(uow, CallService(uow, integration, video, dispatcher, agent_start_label))
            if integration is not None
            else None
        )
        if self._calls is not None:
            self._types |= self._calls.types

    async def run_due(self) -> int:
        """Run every due job once; return how many were claimed."""
        now = self._clock.now()
        jobs = await self._queue.claim_due(now, self._types, MESSAGE_CHANNELS)
        for job in jobs:
            try:
                await self._run(job, now)
            except IntegrationError as exc:
                await self._handle_failure(job, now, exc.code, retryable=exc.retryable)
            except DomainError as exc:  # e.g. integration_not_ready: retrying will not help
                await self._handle_failure(job, now, exc.code.value, retryable=False)
            except Exception as exc:  # any failure is retried; the type is logged, not the text
                await self._handle_failure(job, now, type(exc).__name__)
            else:
                await self._queue.complete(job.id)
        return len(jobs)

    async def _run(self, job: ClaimedJob, now: datetime) -> None:
        """Execute one job."""
        if job.type in CALL_JOBS and self._calls is not None:
            await self._calls.run(job)
            return
        if job.type is JobType.PURGE_PII:
            async with self._uow.locked() as tx:
                await tx.purge_personal_data(job.booking_id, now)
            log.info("job.purged", job_id=job.id, booking_id=job.booking_id)
            return
        channel = job.channel or ChannelKind.EMAIL
        async with self._uow.read() as tx:
            booking = await tx.get_booking(job.booking_id)
            group = await tx.get_group_by_id(booking.group_id) if booking else None
            closure = (
                await tx.get_closure(booking.closure_id)
                if booking and booking.closure_id is not None
                else None
            )
            texts = await tx.get_group_texts(group.id) if group else {}
        contact = booking.contact if booking else None
        if (
            booking is None
            or group is None
            or contact is None
            or (channel is ChannelKind.SMS and contact.phone is None)
        ):
            log.info("job.skipped", job_id=job.id, reason="no_recipient")
            return
        if is_stale(job.pinned_start, booking, job.type):
            log.info("job.skipped", job_id=job.id, reason="stale")
            return
        if self._senders is None:  # pragma: no cover - message jobs are not claimed then
            raise RuntimeError("no sender directory")
        sender = await self._senders.sender(group.organisation_id, channel)
        if sender is None:
            raise DomainError(ErrorCode.CHANNEL_NOT_CONFIGURED)
        message = self._composer.compose(
            job.type, booking, group, now, channel=channel, texts=texts, closure=closure
        )
        await sender.send(message)
        log.info("job.sent", job_id=job.id, type=job.type.value, channel=channel.value)

    async def _handle_failure(
        self, job: ClaimedJob, now: datetime, error: str, *, retryable: bool = True
    ) -> None:
        """Retry with backoff, or fail after the last attempt (refusals fail at once)."""
        if retryable and job.attempts <= len(BACKOFF):
            await self._queue.retry(job.id, now + BACKOFF[job.attempts - 1], error)
            log.warning("job.retry", job_id=job.id, attempt=job.attempts, error=error)
            return
        await self._queue.fail(job.id, error)
        log.error("job.failed", job_id=job.id, type=job.type.value, error=error)
        if job.type in CALL_JOBS and self._calls is not None:
            await self._calls.record_failure(job, error)
