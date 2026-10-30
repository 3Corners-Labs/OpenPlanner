"""Run due jobs: message jobs through a ``MessageSender``, and the PII purge.

Handled: confirmation, change, reminder (email and SMS), cancellation, the emergency closure
messages (suspension, update, reinstatement; ADR 0024), purge, and, when Webex and WxCC are
configured, meetings and work items (``app.jobs.calls``). Jobs of an unconfigured
integration are not claimed; they wait. A message for an organisation without a sender on
its channel fails at once as ``channel_not_configured`` (ADR 0022).

    claim due ─► stale? (pinned start changed / not confirmed / purged) ─► done, nothing sent
             ─► purge ─► NULL PII
             ─► message ─► sender of the group's organisation and the job's channel
                       ─► compose (group texts first) ─► send ─► done
             ─► meeting / work item ─► app.jobs.calls
             failure ─► retry after 1, 5, 15 min ─► failed (refusals fail at once)

Every outcome (sent, skipped with its reason, retry, failed with its error code) is also a
system event in the audit log at the booking's group (ADR 0031), so admins can troubleshoot
their own groups without reading the container logs. Expired events are pruned at most
once an hour. Writing the event never decides the job's fate: if it fails, it is logged.

Each job runs in its own trace, bound to its booking's organisation and group, so its log
lines and its system event share one ``trace.id`` and can be filtered per tenant (ADR 0040).
While that group or its organisation is in diagnostic mode, each job also writes a debug event
``job.run`` with its type, attempt and duration. Pruning removes every category of event after
its own retention period.
"""

from collections.abc import Callable, Collection
from datetime import datetime, timedelta
from typing import Protocol

import structlog

from app.adapters.db.jobs import ClaimedJob
from app.domain import booking_changes
from app.domain.audit import Outcome
from app.domain.call_service import CallService
from app.domain.channels import ChannelKind
from app.domain.diagnostics import DiagnosticLog
from app.domain.errors import DomainError, ErrorCode
from app.domain.event_context import current, trace_scope
from app.domain.integration import IntegrationService
from app.domain.job_plan import TEAM_INVITES, JobType, is_stale
from app.domain.ports import (
    Clock,
    ContactCenterDispatcher,
    IntegrationError,
    SenderDirectory,
    UnitOfWork,
    VideoProvider,
)
from app.domain.system_log import EventRetention, SystemLog
from app.domain.tunable import value_of
from app.i18n import DEFAULT_LOCALE
from app.i18n.dates import clock_scope, use_clock
from app.jobs.calls import CALL_JOBS, STALE, CallJobs
from app.jobs.messages import MESSAGE_KINDS, MessageComposer
from app.web.call_labels import agent_start_label

log = structlog.get_logger()

BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
"""Delay before attempt 2, 3 and 4; after that the job fails."""


PRUNE_EVERY = timedelta(hours=1)
"""How often the runner prunes expired events."""


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


NO_RECIPIENT = "no_recipient"
"""Skip reason: the booking, its group, its contact details or (for SMS) its phone is gone."""

NO_TEAM_INVITE = "no_team_invite"
"""Skip reason: the group no longer has an own queue with an invite address (ADR 0043)."""

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
        retention: How long each category of event is kept (ADR 0040, part 4).
        clock_style: The time format of an organisation by id (ADR 0042 step 5); messages
            write times in their group's organisation's format.
        product_footer: Whether an organisation (by id) keeps "Appointments via OpenPlanner"
            in its emails (ADR 0060 §6); ``None`` keeps it.
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
        retention: EventRetention | Callable[[], EventRetention] | None = None,
        clock_style: Callable[[int], str] | None = None,
        product_footer: Callable[[int], bool] | None = None,
    ) -> None:
        """Store the collaborators and decide which job types this worker can handle."""
        self._queue = queue
        self._system_log = SystemLog(uow, clock)
        self._retention = retention or EventRetention()
        self._clock_style = clock_style
        self._product_footer = product_footer
        self._diagnostics = DiagnosticLog(uow, clock)
        self._pruned_at: datetime | None = None
        self._uow = uow
        self._clock = clock
        self._senders = senders
        self._composer = composer
        self._types: set[JobType] = {JobType.PURGE_PII, JobType.LAPSE_REBOOK}
        if senders is not None:
            self._types |= set(MESSAGE_KINDS) | TEAM_INVITES
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
        await self._prune(now)
        jobs = await self._queue.claim_due(now, self._types, MESSAGE_CHANNELS)
        for job in jobs:
            with trace_scope():
                await self._bind(job)
                started = self._clock.now()
                await self._handle(job, now)
                await self._debug(job, self._clock.now() - started)
        return len(jobs)

    async def _debug(self, job: ClaimedJob, took: timedelta) -> None:
        """Write a debug event for the job when its group is in diagnostic mode (ADR 0040)."""
        context = current()
        detail = (
            f"type:{job.type.value},attempt:{job.attempts},ms:{took // timedelta(milliseconds=1)}"
        )
        try:
            await self._diagnostics.debug(
                "job.run",
                context.organisation,
                context.group,
                booking_id=job.booking_id,
                detail=detail,
            )
        except Exception as exc:  # debug detail never stops a job
            log.warning("diagnostics.failed", job_id=job.id, error=type(exc).__name__)

    async def _handle(self, job: ClaimedJob, now: datetime) -> None:
        """Run one job and settle it: done, skipped, retried or failed, with its event."""
        try:
            skipped = await self._run(job, now)
        except IntegrationError as exc:
            await self._handle_failure(
                job, now, exc.code, retryable=exc.retryable, message=exc.reason
            )
        except DomainError as exc:  # e.g. integration_not_ready: retrying will not help
            await self._handle_failure(job, now, exc.code.value, retryable=False)
        except Exception as exc:  # any failure is retried; the type is logged, not the text
            await self._handle_failure(job, now, type(exc).__name__)
        else:
            await self._queue.complete(job.id)
            outcome = Outcome.SUCCESS if skipped is None else Outcome.SKIPPED
            await self._record(job, outcome, skipped or "")

    async def _bind(self, job: ClaimedJob) -> None:
        """Tag the job's trace with its booking's organisation and group; a failure is logged."""
        try:
            await self._system_log.bind_level(job.booking_id)
        except Exception as exc:  # tagging helps troubleshooting; it never stops a job
            log.warning("system_log.bind_failed", job_id=job.id, error=type(exc).__name__)

    async def _run(self, job: ClaimedJob, now: datetime) -> str | None:
        """Execute one job; return why it was skipped, or ``None`` when it was done."""
        if job.type in CALL_JOBS and self._calls is not None:
            return await self._calls.run(job)
        if job.type is JobType.PURGE_PII:
            async with self._uow.locked() as tx:
                await tx.purge_personal_data(job.booking_id, now)
            log.info("job.purged", job_id=job.id, booking_id=job.booking_id)
            return None
        if job.type is JobType.LAPSE_REBOOK:  # ADR 0044: no new time chosen in time
            async with self._uow.locked() as tx:
                booking = await tx.get_booking(job.booking_id)
                lapsed = booking is not None and await booking_changes.lapse(tx, booking, now)
            if not lapsed:
                log.info("job.skipped", job_id=job.id, reason=STALE)
                return STALE
            log.info("job.lapsed", job_id=job.id, booking_id=job.booking_id)
            return None
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
            organisation = (
                await tx.get_organisation_by_id(group.organisation_id)
                if group and job.type in TEAM_INVITES
                else None
            )
        contact = booking.contact if booking else None
        if (
            booking is None
            or group is None
            or contact is None
            or (channel is ChannelKind.SMS and contact.phone is None)
        ):
            log.info("job.skipped", job_id=job.id, reason=NO_RECIPIENT)
            return NO_RECIPIENT
        if is_stale(job.pinned_start, booking, job.type):
            log.info("job.skipped", job_id=job.id, reason=STALE)
            return STALE
        if job.type in TEAM_INVITES and not group.sends_team_invite:
            log.info("job.skipped", job_id=job.id, reason=NO_TEAM_INVITE)
            return NO_TEAM_INVITE
        if self._senders is None:  # pragma: no cover - message jobs are not claimed then
            raise RuntimeError("no sender directory")
        sender = await self._senders.sender(group.organisation_id, channel)
        if sender is None:
            raise DomainError(ErrorCode.CHANNEL_NOT_CONFIGURED)
        with clock_scope():
            if self._clock_style is not None:
                use_clock(self._clock_style(group.organisation_id))
            if job.type in TEAM_INVITES:
                locale = organisation.default_locale if organisation else DEFAULT_LOCALE
                message = self._composer.compose_team_invite(job.type, booking, group, now, locale)
            else:
                message = self._composer.compose(
                    job.type,
                    booking,
                    group,
                    now,
                    channel=channel,
                    texts=texts,
                    closure=closure,
                    product_footer=self._product_footer is None
                    or self._product_footer(group.organisation_id),
                )
        await sender.send(message)
        log.info("job.sent", job_id=job.id, type=job.type.value, channel=channel.value)
        return None

    async def _handle_failure(
        self,
        job: ClaimedJob,
        now: datetime,
        error: str,
        *,
        retryable: bool = True,
        message: str = "",
    ) -> None:
        """Retry with backoff, or fail after the last attempt (refusals fail at once).

        ``message`` is the provider's own reason (ADR 0065); it goes with the event.
        """
        if retryable and job.attempts <= len(BACKOFF):
            await self._queue.retry(job.id, now + BACKOFF[job.attempts - 1], error)
            log.warning("job.retry", job_id=job.id, attempt=job.attempts, error=error)
            await self._record(job, Outcome.RETRY, error, message)
            return
        await self._queue.fail(job.id, error)
        log.error("job.failed", job_id=job.id, type=job.type.value, error=error)
        await self._record(job, Outcome.FAILURE, error, message)
        if job.type in CALL_JOBS and self._calls is not None:
            await self._calls.record_failure(job, error, message)

    async def _record(
        self, job: ClaimedJob, outcome: Outcome, reason: str, message: str = ""
    ) -> None:
        """Write the job's outcome as a system event; a failure here is only logged."""
        detail = f"attempt:{job.attempts}"
        if job.channel is not None:
            detail += f",channel:{job.channel.value}"
        try:
            await self._system_log.record(
                f"job.{job.type.value}",
                outcome,
                booking_id=job.booking_id,
                reason=reason,
                detail=detail,
                message=message,
            )
        except Exception as exc:  # the job's own outcome is already settled
            log.warning("system_log.failed", job_id=job.id, error=type(exc).__name__)

    async def _prune(self, now: datetime) -> None:
        """Delete expired events of every category, at most once per ``PRUNE_EVERY``."""
        if self._pruned_at is not None and now - self._pruned_at < PRUNE_EVERY:
            return
        self._pruned_at = now
        try:
            removed = await self._system_log.prune(value_of(self._retention))
        except Exception as exc:  # pruning is housekeeping; jobs come first
            log.warning("system_log.prune_failed", error=type(exc).__name__)
            return
        if removed:
            log.info("system_log.pruned", removed=removed)
