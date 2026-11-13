"""Recording what Webex and WxCC report, and showing it to admins (ADR 0010, 0017, 0018).

Webhook handlers call ``record_meeting_event`` and ``record_task_event``; jobs call
``record``. Each call writes the event and the updated ``CallStatus`` in one transaction.
Webex and WxCC events carry an ``event_key``: it is remembered for ``WEBHOOK_MEMORY`` in the
same transaction, so a replayed or redelivered event changes nothing the second time.
Admins see a booking's timeline only with ``bookings.view`` on the group of the page's address,
and only a booking of that group (ADR 0014).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, AuditFilter
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.availability import utc_bounds
from app.domain.booking import Booking
from app.domain.call_usage import record_call_event
from app.domain.calls import (
    TRIAL_MEETING_EVENTS,
    BookingEvent,
    BookingOverview,
    CallStatus,
    EventKind,
    apply_event,
    new_status,
)
from app.domain.contact_center import ContactCenterKind
from app.domain.job_plan import JobSummary
from app.domain.lookups import group_booking
from app.domain.ports import REASON_LIMIT, Clock, ProbeRegistry, Transaction, UnitOfWork
from app.domain.request_log import REQUEST_EVENT
from app.domain.staff_usage import StaffUsage

WEBHOOK_MEMORY = timedelta(days=30)
"""How long a Webex event key is remembered; older keys are forgotten on the next event."""

WEBEX_SOURCE = "webex"


class TaskWork(Protocol):
    """The request work behind a contact-center task (ADR 0121 step 6b), inside the lock."""

    async def on_task_event(
        self, tx: Transaction, booking: Booking, kind: EventKind, at: datetime
    ) -> None:
        """Apply a platform event of ``booking``'s task to its contact-center attempt."""
        ...

    async def adopt(
        self,
        tx: Transaction,
        origin: str,
        task_id: str,
        provider: ContactCenterKind,
        at: datetime,
        organisation_id: int | None = None,
    ) -> Booking | None:
        """Match an unknown task to an attempt by its origin; ``None`` when it is not ours."""
        ...


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
        staff_usage: StaffUsage | None = None,
        work: TaskWork | None = None,
    ) -> None:
        """Store the collaborators; ``staff_usage`` adds a team member's usage facts.

        ``work`` applies task events to the request work of new flows (ADR 0121 step 6b).
        """
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._version = version
        self._probes = probes
        self._staff_usage = staff_usage
        self._work = work

    @property
    def staff_usage(self) -> StaffUsage | None:
        """The writer of a team member's usage facts, for the poll that records call events too."""
        return self._staff_usage

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
            if not await _claim(tx, WEBEX_SOURCE, event_key, at):
                return EventOutcome.DUPLICATE
            await self._record(tx, booking.id, kind, at, detail)
        return EventOutcome.RECORDED

    async def record_task_event(
        self,
        task_id: str,
        kind: EventKind,
        at: datetime,
        detail: str = "",
        *,
        event_key: str = "",
        origin: str = "",
        provider: ContactCenterKind,
        source: str,
        organisation_id: int | None = None,
    ) -> EventOutcome:
        """Record a work item event once per ``event_key``; ``UNKNOWN`` for foreign tasks.

        The task is looked up at ``provider`` only (ADR 0032 point 6): a task id of one
        platform never finds a booking whose task is at another. ``source`` is the platform's
        code from its adapter, under which its event keys are remembered. With
        ``organisation_id`` (an organisation's own webhook address, ADR 0032 point 10) a task of
        another organisation's booking is unknown here.

        A ``task:new`` of a booking's task is ``IGNORED``: the dispatch job recorded the
        creation when it got the id. An unknown task may be the group check's probe (ADR
        0082): its event is noted for the check and the outcome is ``PROBE``. Like a Webex
        event, a replayed or redelivered WxCC event is ``DUPLICATE`` (ADR 0082 point 5).

        With request work (ADR 0121 step 6b) the event also moves the booking's contact-center
        attempt, and an unknown task whose ``origin`` names an attempt in doubt or a fenced one
        is matched to it (adopted, or ended at the platform).
        """
        async with self._uow.locked() as tx:
            booking = await tx.get_booking_by_task(task_id, provider)
            if booking is None and self._work is not None and origin:
                booking = await self._work.adopt(tx, origin, task_id, provider, at, organisation_id)
            if booking is not None and organisation_id is not None:
                group = await tx.get_group_by_id(booking.group_id)
                if group is None or group.organisation_id != organisation_id:
                    booking = None
            if booking is not None and booking.id is not None:
                if kind is EventKind.TASK_CREATED:
                    return EventOutcome.IGNORED
                if not await _claim(tx, source, event_key, at):
                    return EventOutcome.DUPLICATE
                await self._record(tx, booking.id, kind, at, detail)
                if self._work is not None:
                    await self._work.on_task_event(tx, booking, kind, at)
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
        group = (await self._admin.authorize_group(principal, permission, slug)).group
        # Midnight to midnight in group time: a day can last 23 or 25 hours (DST).
        lower, upper = utc_bounds(group, day, day)
        async with self._uow.read() as tx:
            return await tx.list_bookings(scope_for(principal, permission), group.id, lower, upper)

    async def timeline(self, principal: Principal, slug: str, booking_id: int) -> Timeline:
        """Return the timeline of booking ``booking_id`` of group ``slug``.

        ``bookings.view`` is decided on group ``slug``, the group of the page's address, never
        on the booking's own group: another group's booking is not found, the same answer as
        an unknown id, so an id tried through this group's address reveals nothing and files
        no refusal under the other group.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found`` (from the authorisation);
                ``booking_not_found``, also for a booking of another group.
        """
        permission = Permission.BOOKINGS_VIEW
        require(await self._admin.authorize(principal, permission, group_slug=slug))
        async with self._uow.read() as tx:
            booking, _ = await group_booking(tx, slug, booking_id)
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
        status = await tx.get_call_status(booking_id) or new_status(booking_id)
        after = apply_event(status, kind, at)
        await record_call_event(
            tx, booking_id, kind, status, after, at, detail, staff=self._staff_usage
        )
        await tx.add_booking_event(
            BookingEvent(booking_id, at, kind, detail[:128], message[:REASON_LIMIT], self._version)
        )
        await tx.save_call_status(after)


async def _claim(tx: Transaction, source: str, event_key: str, at: datetime) -> bool:
    """Remember ``event_key`` of ``source``; ``False`` when it was seen before (a replay).

    Without a key there is nothing to remember and the event always counts. Keys older than
    ``WEBHOOK_MEMORY`` are forgotten in the same transaction.
    """
    if not event_key:
        return True
    await tx.forget_webhook_events(at - WEBHOOK_MEMORY)
    return await tx.claim_webhook_event(source, event_key, at)
