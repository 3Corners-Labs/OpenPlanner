"""Requests, their work items and the request-level personal data (ADR 0121 step 5).

A ``ServiceRequest`` is the identity of one request for a service, made under one flow revision,
with an optional reservation (the existing booking). Staff handle it through ``WorkItem`` s: one
attempt each, at most one active at a time. Reservation, handling and personal data are
separate states (ADR 0121 point 7); the booking keeps its own status.

    queued ─claim─► assigned ─start─► in_progress ─complete─► completed
      ▲  release / lease expiry ┘           └─watchdog─► recovery_required ─complete─► completed
    queued / assigned ─watchdog─► expired
    not started, booking no longer holds a place ─► cancelled

A request of an appointment is made at its first claim; before that the booking alone is the
reservation, so no booking path needs to know about requests (design R1). Its personal data
stays on the booking; a request without an appointment keeps it in the request-level record
(``RequestPersonalData``, decision D1). ``contact_of`` reads one of the two, never both.

The occupation of a pool is part of the work item that holds it (design R2): started and not
ended, it is a commitment ``in_progress`` over ``[started_at, planned_until)`` with the request's
key, the same key as the booking's hold, so the pool counts one unit (invariant 3). Ending the
work item releases it, exactly once; ``recovery_required`` is not ended and keeps it (A34).

Pure Python, no I/O. The build brief is ``docs/service-flows-requests-design.md``.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.booking import BookingStatus, Contact
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef
from app.domain.form_fields import Answers
from app.domain.job_plan import PURGE_AFTER
from app.domain.pool_accounting import Commitment, CommitmentKey
from app.domain.service_flows import FlowRevision, IntakeMode, InteractionKind


class HandlingState(StrEnum):
    """Where the handling of a request stands."""

    QUEUED = "queued"
    """Waiting for staff; no active work item."""
    ASSIGNED = "assigned"
    """Claimed, treatment not started; the claim holds until the lease ends."""
    IN_PROGRESS = "in_progress"
    RECOVERY_REQUIRED = "recovery_required"
    """Started and still not completed at the watchdog deadline; its unit stays held."""
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    """Never started before the watchdog deadline."""


TERMINAL: frozenset[HandlingState] = frozenset(
    {HandlingState.COMPLETED, HandlingState.CANCELLED, HandlingState.EXPIRED}
)
"""Handling states a request never leaves."""


class WorkItemState(StrEnum):
    """Where one attempt of staff to handle a request stands."""

    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETED = "completed"
    RELEASED = "released"
    LEASE_EXPIRED = "lease_expired"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


ACTIVE_ITEM: frozenset[WorkItemState] = frozenset(
    {WorkItemState.ASSIGNED, WorkItemState.IN_PROGRESS, WorkItemState.RECOVERY_REQUIRED}
)
"""Work item states that count as the request's one active work item (the partial unique index
``uq_work_items_active`` is the backstop)."""


class PersonalDataState(StrEnum):
    """The state of the request-level record of personal data (decision D1)."""

    NOT_COLLECTED = "not_collected"
    """No record: nothing was asked, or the data lives on the booking of an appointment."""
    PRESENT = "present"
    DELETED = "deleted"
    """The record was deleted on time; missing data is never mistaken for this (point 7)."""


@dataclass(frozen=True)
class RequestPersonalData:
    """The personal data of a request without an appointment (decision D1).

    The field names are those of ``privacy.REQUEST_DATA_FIELDS`` (a subset of the booking's), so
    the log mask and the deletion list stay one list. Every field may be ``None``: what a flow
    does not collect is not stored, and nothing is invented for it (A20). Excluded from
    ``repr`` like ``Contact``.
    """

    first_name: str | None = field(default=None, repr=False)
    last_name: str | None = field(default=None, repr=False)
    email: str | None = field(default=None, repr=False)
    phone: str | None = field(default=None, repr=False)
    answers: Answers | None = field(default=None, repr=False)

    @property
    def empty(self) -> bool:
        """Whether nothing was collected: then no record is stored at all."""
        return all(
            value is None
            for value in (self.first_name, self.last_name, self.email, self.phone, self.answers)
        )


@dataclass(frozen=True)
class WorkItem:
    """One attempt of staff to handle a request.

    Attributes:
        id: Database id, ``None`` before it is stored.
        request_id: The request it handles.
        state: Where it stands.
        assigned_to: The agent's pseudonym (``tokens.agent_pseudonym``), never a name; emptied
            with the personal data (``None`` then).
        assigned_at: When it was claimed (UTC).
        lease_until: Until when the claim holds before treatment starts.
        started_at: When treatment started.
        pool_id: The counted pool whose unit it occupies once started.
        planned_until: The planned end of the occupation: start plus length plus wrap-up.
        start_conflict: It started during a colleague's overrun (ADR 0122 decision 11).
        ended_at: When it stopped being active: the release of its occupation.
    """

    id: int | None
    request_id: int
    state: WorkItemState
    assigned_to: str | None = field(repr=False)
    assigned_at: datetime
    lease_until: datetime | None
    started_at: datetime | None = None
    pool_id: int | None = None
    planned_until: datetime | None = None
    start_conflict: bool = False
    ended_at: datetime | None = None

    @property
    def active(self) -> bool:
        """Whether it is the request's active work item."""
        return self.state in ACTIVE_ITEM

    @property
    def started(self) -> bool:
        """Whether treatment started (it may have ended since)."""
        return self.started_at is not None

    def lease_over(self, now: datetime) -> bool:
        """Whether it is claimed but not started and its lease has run out at ``now``."""
        return (
            self.state is WorkItemState.ASSIGNED
            and self.lease_until is not None
            and self.lease_until <= now
        )

    def commitment(self, key: CommitmentKey) -> Commitment | None:
        """Return the unit it occupies, ``in_progress``, or ``None`` when it holds none.

        Started, on a pool, and not ended: ``recovery_required`` holds its unit too (A34).
        """
        if (
            self.ended_at is not None
            or self.started_at is None
            or self.pool_id is None
            or self.planned_until is None
        ):
            return None
        return Commitment(key, self.started_at, self.planned_until, in_progress=True)


@dataclass(frozen=True)
class ServiceRequest:
    """One request for a service, made under one flow revision.

    Attributes:
        id: Database id, ``None`` before it is stored.
        organisation_id: The organisation of its service.
        group_id: The service (group) it was made for.
        flow: The flow and revision it was made under; it never changes (A23).
        intake: Appointment or on demand.
        booking_id: The reservation, ``None`` for a request without an appointment.
        handling: Where its handling stands.
        created_at: When it was made (UTC).
        watchdog_at: The reservation's end (or the acceptance) plus ``request_unresolved_hours``,
            fixed when it was made.
        resolved_at: When it reached a terminal state.
        personal_data: The state of the request-level record.
        data_deleted_at: When that record was deleted.
    """

    id: int | None
    organisation_id: int
    group_id: int
    flow: FlowRef
    intake: IntakeMode
    booking_id: int | None
    handling: HandlingState
    created_at: datetime
    watchdog_at: datetime
    resolved_at: datetime | None = None
    personal_data: PersonalDataState = PersonalDataState.NOT_COLLECTED
    data_deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        """Refuse an appointment without a booking and a resolved time out of step.

        Raises:
            DomainError: ``invalid_configuration`` with field ``booking_id`` or ``resolved_at``.
        """
        if (self.intake is IntakeMode.APPOINTMENT) != (self.booking_id is not None):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "booking_id")
        if (self.handling in TERMINAL) != (self.resolved_at is not None):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "resolved_at")

    @property
    def terminal(self) -> bool:
        """Whether the request is finished for good."""
        return self.handling in TERMINAL

    @property
    def key(self) -> CommitmentKey:
        """The pool commitment key of the request: its booking's, so the pool counts one unit."""
        if self.booking_id is not None:
            return ("booking", self.booking_id)
        return ("request", self.id or 0)


@dataclass(frozen=True)
class UnclaimedBooking:
    """A confirmed booking on a counted pool that has no request yet: what the watchdog needs.

    Attributes:
        booking_id: The booking.
        organisation_id: Its service's organisation, whose watchdog hours apply.
        group_id: Its service.
        flow: The flow and revision it was made under.
        end: The end of the appointment.
    """

    booking_id: int
    organisation_id: int
    group_id: int
    flow: FlowRef
    end: datetime


def watchdog_deadline(anchor: datetime, unresolved_hours: int) -> datetime:
    """Return the watchdog moment: ``anchor`` (reservation end or acceptance) plus the hours."""
    return anchor + timedelta(hours=unresolved_hours)


def appointment_request(
    *,
    organisation_id: int,
    group_id: int,
    flow: FlowRef,
    booking_id: int,
    booking_end: datetime,
    now: datetime,
    unresolved_hours: int,
    handling: HandlingState = HandlingState.QUEUED,
) -> ServiceRequest:
    """Return the new request of an appointment: its data stays on the booking (D1).

    ``handling`` is ``EXPIRED`` (with ``resolved_at`` now) when the watchdog makes the request
    of a booking nobody claimed (A33).
    """
    return ServiceRequest(
        id=None,
        organisation_id=organisation_id,
        group_id=group_id,
        flow=flow,
        intake=IntakeMode.APPOINTMENT,
        booking_id=booking_id,
        handling=handling,
        created_at=now,
        watchdog_at=watchdog_deadline(booking_end, unresolved_hours),
        resolved_at=now if handling in TERMINAL else None,
    )


def _resolved(request: ServiceRequest, handling: HandlingState, now: datetime) -> ServiceRequest:
    """Return ``request`` in ``handling``, with ``resolved_at`` set when that is terminal."""
    return replace(request, handling=handling, resolved_at=now if handling in TERMINAL else None)


def _ended(item: WorkItem, state: WorkItemState, now: datetime) -> WorkItem:
    """Return ``item`` ended in ``state`` at ``now``: no lease, its occupation released."""
    return replace(item, state=state, lease_until=None, ended_at=now)


def claim(
    request: ServiceRequest,
    active: WorkItem | None,
    *,
    now: datetime,
    agent: str,
    lease_minutes: int,
) -> tuple[ServiceRequest, WorkItem]:
    """Return the request assigned and its new work item, with a lease until the start.

    Raises:
        DomainError: ``booking_not_active`` for a finished request, ``already_claimed`` when it
            has an active work item.
    """
    if request.terminal or request.handling is HandlingState.RECOVERY_REQUIRED:
        raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
    if active is not None or request.handling is not HandlingState.QUEUED:
        raise DomainError(ErrorCode.ALREADY_CLAIMED)
    item = WorkItem(
        id=None,
        request_id=request.id or 0,
        state=WorkItemState.ASSIGNED,
        assigned_to=agent,
        assigned_at=now,
        lease_until=now + timedelta(minutes=lease_minutes),
    )
    return _resolved(request, HandlingState.ASSIGNED, now), item


def check_startable(item: WorkItem | None, now: datetime) -> WorkItem:
    """Return the work item that may start now: assigned and within its lease.

    Raises:
        DomainError: ``not_claimed`` without one, ``request_in_progress`` when it started
            already, ``lease_expired`` when its lease ran out.
    """
    if item is None or not item.active:
        raise DomainError(ErrorCode.NOT_CLAIMED)
    if item.state is not WorkItemState.ASSIGNED:
        raise DomainError(ErrorCode.REQUEST_IN_PROGRESS)
    if item.lease_over(now):
        raise DomainError(ErrorCode.LEASE_EXPIRED)
    return item


def start(
    request: ServiceRequest,
    item: WorkItem | None,
    *,
    now: datetime,
    pool_id: int,
    planned_until: datetime,
    conflict: bool,
) -> tuple[ServiceRequest, WorkItem]:
    """Return the request and its work item in treatment, occupying a unit of ``pool_id``.

    Whether the pool lets it start (``pool_accounting.check_start``) is the caller's question,
    asked under the lock; ``conflict`` records a start during a colleague's overrun.

    Raises:
        DomainError: as ``check_startable``.
    """
    startable = check_startable(item, now)
    started = replace(
        startable,
        state=WorkItemState.IN_PROGRESS,
        lease_until=None,
        started_at=now,
        pool_id=pool_id,
        planned_until=planned_until,
        start_conflict=conflict,
    )
    return _resolved(request, HandlingState.IN_PROGRESS, now), started


def complete(
    request: ServiceRequest, item: WorkItem | None, *, now: datetime
) -> tuple[ServiceRequest, WorkItem]:
    """Return the request completed and its work item ended, releasing the occupation.

    Raises:
        DomainError: ``not_claimed`` without an active work item, ``request_not_started`` for
            one that has not started.
    """
    if item is None or not item.active:
        raise DomainError(ErrorCode.NOT_CLAIMED)
    if item.state is WorkItemState.ASSIGNED:
        raise DomainError(ErrorCode.REQUEST_NOT_STARTED)
    return (
        _resolved(request, HandlingState.COMPLETED, now),
        _ended(item, WorkItemState.COMPLETED, now),
    )


def release(
    request: ServiceRequest,
    item: WorkItem | None,
    *,
    now: datetime,
    state: WorkItemState = WorkItemState.RELEASED,
) -> tuple[ServiceRequest, WorkItem]:
    """Return the request back in the queue and its unstarted work item ended in ``state``.

    ``state`` is ``RELEASED`` (an agent or supervisor) or ``LEASE_EXPIRED`` (the watchdog round).

    Raises:
        DomainError: ``not_claimed`` without an active work item, ``request_in_progress`` for
            a started one: running treatment is completed, never released.
    """
    if item is None or not item.active:
        raise DomainError(ErrorCode.NOT_CLAIMED)
    if item.state is not WorkItemState.ASSIGNED:
        raise DomainError(ErrorCode.REQUEST_IN_PROGRESS)
    return _resolved(request, HandlingState.QUEUED, now), _ended(item, state, now)


@dataclass(frozen=True)
class WatchdogOutcome:
    """What the watchdog round does with one request.

    Attributes:
        request: The request in its new state.
        item: Its work item in its new state, ``None`` when it has none or it is unchanged.
        clear_claim: Whether the booking's claim must be cleared (the work went back or ended
            before treatment).
    """

    request: ServiceRequest
    item: WorkItem | None
    clear_claim: bool


class BookingPhase(StrEnum):
    """What a request's booking means for its handling (the reservation's own state)."""

    ACTIVE = "active"
    """Confirmed: the appointment goes ahead (and every request without a booking)."""
    PAUSED = "paused"
    """On hold: suspended by a closure, awaiting a new time or awaiting confirmation. The
    request stays open and waits; its deadline follows the booking when it goes ahead again."""
    ENDED = "ended"
    """Cancelled, refused, completed or no-show: unstarted work ends."""


_PAUSED = frozenset(
    {BookingStatus.SUSPENDED, BookingStatus.AWAITING_REBOOK, BookingStatus.PENDING_CONFIRMATION}
)


def booking_phase(status: BookingStatus | None) -> BookingPhase:
    """Return the phase of a request whose booking has ``status`` (``None``: no booking)."""
    if status is None or status is BookingStatus.CONFIRMED:
        return BookingPhase.ACTIVE
    return BookingPhase.PAUSED if status in _PAUSED else BookingPhase.ENDED


def is_request_work(revision: FlowRevision | None) -> bool:
    """Return whether bookings of ``revision`` are request work: not legacy and without video.

    The one rule of the queue (``RequestWork.handles``) and of the watchdog's unclaimed
    bookings, so a video flow never gets a request (or a false ``expired`` one).
    """
    return (
        revision is not None
        and not revision.legacy
        and revision.interaction_kind is not InteractionKind.VIDEO
    )


def watchdog_outcome(
    request: ServiceRequest,
    item: WorkItem | None,
    *,
    phase: BookingPhase = BookingPhase.ACTIVE,
    now: datetime,
    deadline: datetime | None = None,
) -> WatchdogOutcome | None:
    """Return what the watchdog round changes for ``request`` at ``now``, or ``None``.

    ``item`` is the request's active work item, ``phase`` its booking's (``booking_phase``) and
    ``deadline`` the watchdog moment as of now: the booking's current end plus the hours, so a
    booking that went ahead again at another time moves it (``None``: ``watchdog_at``). The
    answer carries the deadline in ``watchdog_at``. In this order:

    * a terminal request, or one in ``recovery_required``, is left alone;
    * started work past the deadline becomes ``recovery_required`` and keeps its unit (A34),
      whatever the booking does (the treatment really occupies someone, design R7);
    * not started and the booking ended (cancelled by any path, an older image included):
      ``cancelled``;
    * not started, the booking going ahead and past the deadline: ``expired`` (A33); while the
      booking is on hold (suspended, awaiting a new time or confirmation) no deadline runs and
      the request waits for it;
    * claimed, not started and the lease is over: back to ``queued`` (``lease_expired``).
    """
    if request.terminal or request.handling is HandlingState.RECOVERY_REQUIRED:
        return None
    due = deadline or request.watchdog_at
    current = replace(request, watchdog_at=due)
    started = item is not None and item.state is WorkItemState.IN_PROGRESS
    if started and item is not None:
        if now < due:
            return None
        recovering = replace(item, state=WorkItemState.RECOVERY_REQUIRED)
        return WatchdogOutcome(
            _resolved(current, HandlingState.RECOVERY_REQUIRED, now), recovering, False
        )
    if phase is BookingPhase.ENDED:
        ended = _ended(item, WorkItemState.CANCELLED, now) if item else None
        return WatchdogOutcome(_resolved(current, HandlingState.CANCELLED, now), ended, True)
    if phase is BookingPhase.ACTIVE and now >= due:
        ended = _ended(item, WorkItemState.EXPIRED, now) if item else None
        return WatchdogOutcome(_resolved(current, HandlingState.EXPIRED, now), ended, True)
    if item is not None and item.lease_over(now):
        back, expired = release(current, item, now=now, state=WorkItemState.LEASE_EXPIRED)
        return WatchdogOutcome(back, expired, True)
    return None


def data_deletion_at(request: ServiceRequest) -> datetime | None:
    """Return when a request without a booking loses its personal data, or ``None`` with one.

    ``min(resolved_at, watchdog_at) + 24 h`` (fixed, ADR 0121 settings): a request that is still
    being handled at its watchdog deadline loses its data all the same; its unit stays held
    (A34). It covers the request-level record and the agents' pseudonyms on its work items,
    so it applies whether or not a record was collected. A request with a booking keeps its data
    on the booking, whose own purge moment applies (``retention.purge_moment``) and also empties
    the pseudonyms.
    """
    if request.booking_id is not None:
        return None
    anchor = min(request.resolved_at or request.watchdog_at, request.watchdog_at)
    return anchor + PURGE_AFTER


@dataclass(frozen=True)
class RequestContact:
    """The personal data of a request as staff may read it, from one source.

    Attributes:
        state: Present, never collected, or deleted.
        data: The fields, ``None`` unless ``state`` is present.
    """

    state: PersonalDataState
    data: RequestPersonalData | None = field(default=None, repr=False)


def contact_of(
    request: ServiceRequest,
    *,
    booking_contact: Contact | None,
    booking_purged: bool,
    record: RequestPersonalData | None,
) -> RequestContact:
    """Return the request's personal data from the one place it lives (decision D1).

    A request with a booking reads the booking (``booking_contact``, ``booking_purged`` whether
    the booking's data was deleted) and never ``record``; one without reads ``record`` and its
    own state. A field that was not collected stays ``None``: nothing is invented (A20).
    """
    if request.booking_id is not None:
        if booking_contact is not None:
            return RequestContact(
                PersonalDataState.PRESENT,
                RequestPersonalData(
                    booking_contact.first_name,
                    booking_contact.last_name,
                    booking_contact.email,
                    booking_contact.phone,
                    booking_contact.answers,
                ),
            )
        state = PersonalDataState.DELETED if booking_purged else PersonalDataState.NOT_COLLECTED
        return RequestContact(state)
    if request.personal_data is PersonalDataState.PRESENT and record is not None:
        return RequestContact(PersonalDataState.PRESENT, record)
    if request.personal_data is PersonalDataState.DELETED:
        return RequestContact(PersonalDataState.DELETED)
    return RequestContact(PersonalDataState.NOT_COLLECTED)
