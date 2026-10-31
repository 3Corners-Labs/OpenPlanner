"""Recording what Webex and WxCC report, and showing it to admins (ADR 0010, 0017, 0018).

Webhook handlers call ``record_meeting_event`` and ``record_task_event``; jobs call
``record``. Each call writes the event and the updated ``CallStatus`` in one transaction.
Webex events carry an ``event_key``: it is remembered for ``WEBHOOK_MEMORY`` in the same
transaction, so a replayed or redelivered event changes nothing the second time.
Admins see a booking's timeline only with ``bookings.view`` on its group (ADR 0014).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.domain.admin import AdminService
from app.domain.audit import AuditEvent, AuditFilter
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.availability import utc_bounds
from app.domain.booking import Booking
from app.domain.calls import (
    TRIAL_MEETING_EVENTS,
    BookingEvent,
    BookingOverview,
    CallStatus,
    EventKind,
    apply_event,
    new_status,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import JobSummary
from app.domain.ports import REASON_LIMIT, Clock, ProbeRegistry, Transaction, UnitOfWork
from app.domain.request_log import REQUEST_EVENT

WEBHOOK_MEMORY = timedelta(days=30)
"""How long a Webex event key is remembered; older keys are forgotten on the next event."""

WEBEX_SOURCE = "webex"

REQUEST_LIMIT = 100
"""How many of a booking's request events its page lists (the newest; ADR 0068)."""


class EventOutcome(StrEnum):
    """What happened to a reported event."""

    RECORDED = "recorded"
    UNKNOWN = "unknown"
    """The meeting or task is not OpenPlanner's; nothing is stored."""
    DUPLICATE = "duplicate"
    """The same event was recorded before (replay or redelivery)."""
    IGNORED = "ignored"
    """An event of a known trial meeting that the timeline does not show (its start and end),
    or a task's ``task:new``, which the dispatch job already recorded."""
    PROBE = "probe"
    """An event of the group check's probe work item (ADR 0082): noted for the check, never
    on a booking."""


@dataclass(frozen=True)
class Timeline:
    """A booking with its events and call status, for the admin portal.

    ``jobs`` and ``requests`` (ADR 0068) are the booking's jobs and the requests the platform
    made for it, so an admin sees what ran, what failed and what Webex answered.
    """

    booking: Booking
    events: list[BookingEvent]
    status: CallStatus
    jobs: list[JobSummary] = field(default_factory=list)
    requests: list[AuditEvent] = field(default_factory=list)


class IntegrationService:
    """Records what the platforms report and shows it to admins.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises admin views (``bookings.view``) and audits refusals.
        version: ``APP_VERSION`` of this process, stamped on every timeline event (ADR 0068).
        probes: Where the group check's probe work item waits for its events (ADR 0082);
            ``None`` when no contact center is configured.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        version: str = "",
        probes: ProbeRegistry | None = None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._version = version
        self._probes = probes

    async def record(
        self,
        booking_id: int,
        kind: EventKind,
        at: datetime | None = None,
        detail: str = "",
        *,
        message: str = "",
    ) -> None:
        """Add an event to the booking's timeline and update its call status.

        ``message`` is the provider's own reason for a failure (ADR 0065), if it gave one.
        """
        async with self._uow.locked() as tx:
            await self._record(tx, booking_id, kind, at or self._clock.now(), detail, message)

    async def record_meeting_event(
        self,
        meeting_id: str,
        kind: EventKind,
        at: datetime,
        detail: str = "",
        *,
        event_key: str = "",
    ) -> EventOutcome:
        """Record a meeting event once per ``event_key`` (none: always).

        Webex may report an instance id (``<meetingId>_I_<n>``); it is matched on the series.
        Keys are only remembered for OpenPlanner's meetings: org-owned webhooks also report
        every other meeting of the organisation.

        The booking's trial call (ADR 0071) is a meeting of its own: its events are recorded
        as the trial kinds of ``TRIAL_MEETING_EVENTS`` and never change the call status.
        """
        series = meeting_id.split("_I_", 1)[0]
        async with self._uow.locked() as tx:
            booking = await tx.get_booking_by_meeting(series)
            trial = booking is None
            if booking is None:
                booking = await tx.get_booking_by_trial_meeting(series)
            if booking is None or booking.id is None:
                return EventOutcome.UNKNOWN
            if trial:
                if kind not in TRIAL_MEETING_EVENTS:
                    return EventOutcome.IGNORED
                detail = "host" if kind is EventKind.HOST_JOINED else detail
                kind = TRIAL_MEETING_EVENTS[kind]
            if event_key:
                await tx.forget_webhook_events(at - WEBHOOK_MEMORY)
                if not await tx.claim_webhook_event(WEBEX_SOURCE, event_key, at):
                    return EventOutcome.DUPLICATE
            await self._record(tx, booking.id, kind, at, detail)
        return EventOutcome.RECORDED

    async def record_task_event(
        self, task_id: str, kind: EventKind, at: datetime, detail: str = ""
    ) -> EventOutcome:
        """Record a work item event; ``UNKNOWN`` for tasks OpenPlanner does not know.

        A ``task:new`` of a booking's task is ``IGNORED``: the dispatch job recorded the
        creation when it got the id. An unknown task may be the group check's probe (ADR
        0082): its event is noted for the check and the outcome is ``PROBE``.
        """
        async with self._uow.locked() as tx:
            booking = await tx.get_booking_by_task(task_id)
            if booking is not None and booking.id is not None:
                if kind is EventKind.TASK_CREATED:
                    return EventOutcome.IGNORED
                await self._record(tx, booking.id, kind, at, detail)
                return EventOutcome.RECORDED
        # Outside the write lock: the registry writes in a transaction of its own.
        if self._probes is not None and await self._probes.note(task_id, kind, at):
            return EventOutcome.PROBE
        return EventOutcome.UNKNOWN

    async def status(self, booking_id: int) -> CallStatus:
        """Return a booking's call status (the participant's waiting page; no admin data)."""
        async with self._uow.read() as tx:
            return await tx.get_call_status(booking_id) or new_status(booking_id)

    async def day_overview(
        self, principal: Principal, slug: str, day: date
    ) -> list[BookingOverview]:
        """Return the group's bookings of ``day`` (group time) with their call status.

        Raises:
            DomainError: ``group_not_found`` or ``not_permitted``.
        """
        permission = Permission.BOOKINGS_VIEW
        authorization = await self._admin.authorize(principal, permission, group_slug=slug)
        if not authorization.decision.allowed or authorization.group is None:
            raise DomainError(ErrorCode.NOT_PERMITTED)
        group = authorization.group.group
        # Midnight to midnight in group time: a day can last 23 or 25 hours (DST).
        lower, upper = utc_bounds(group, day, day)
        async with self._uow.read() as tx:
            return await tx.list_bookings(scope_for(principal, permission), group.id, lower, upper)

    async def timeline(self, principal: Principal, booking_id: int) -> Timeline:
        """Return the booking's timeline if ``principal`` may view its group's bookings.

        Raises:
            DomainError: ``booking_not_found`` or ``not_permitted``.
        """
        async with self._uow.read() as tx:
            booking = await tx.get_booking(booking_id)
            group = await tx.get_group_by_id(booking.group_id) if booking else None
        if booking is None or group is None:
            raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
        authorization = await self._admin.authorize(
            principal, Permission.BOOKINGS_VIEW, group_slug=group.slug
        )
        if not authorization.decision.allowed:
            raise DomainError(ErrorCode.NOT_PERMITTED)
        permission = Permission.BOOKINGS_VIEW
        async with self._uow.read() as tx:
            events = await tx.get_booking_events(booking_id)
            status = await tx.get_call_status(booking_id) or new_status(booking_id)
            jobs = await tx.get_booking_jobs(booking_id)
            requests = await tx.list_audit_events(
                scope_for(principal, permission),
                REQUEST_LIMIT,
                AuditFilter(booking_id=booking_id, action=REQUEST_EVENT),
            )
        return Timeline(booking, events, status, jobs, [r.event for r in reversed(requests)])

    async def _record(
        self,
        tx: Transaction,
        booking_id: int,
        kind: EventKind,
        at: datetime,
        detail: str,
        message: str = "",
    ) -> None:
        """Write the event and the updated status in the caller's transaction."""
        await tx.add_booking_event(
            BookingEvent(booking_id, at, kind, detail[:128], message[:REASON_LIMIT], self._version)
        )
        status = await tx.get_call_status(booking_id) or new_status(booking_id)
        await tx.save_call_status(apply_event(status, kind, at))
