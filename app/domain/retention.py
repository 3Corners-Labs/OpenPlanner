"""Retention overview: whether personal data is removed on time (ADR 0039, ``retention.view``).

Per group in scope it counts the bookings that still hold personal data, how many of them
are past their purge moment, and how many ``purge_pii`` jobs failed, plus when the last purge
ran. Counts and times only: nothing here identifies a booking or contains its contents, so
the auditor role can see it.

A booking's purge moment follows the privacy rules (CLAUDE.md, ADR 0024):

    confirmed / completed / no show / direct call ─► slot end + 24 h
    cancelled                                      ─► cancellation + 24 h
    suspended                                      ─► min(closure end + 30 days, slot end + the
                                                      organisation's maximum); the maximum also
                                                      applies while the closure is open (ADR 0102)
    awaiting a new time (ADR 0044)                 ─► rebook_by + 24 h (it lapses then)
    awaiting confirmation (ADR 0056)               ─► confirm_by + 24 h (it lapses then)

A booking counts as overdue once its purge moment lies more than ``PURGE_GRACE`` in the past,
so a purge that is waiting for the worker or for a retry does not raise an alarm. The nightly
sweep (``app.domain.retention_sweep``) purges what is overdue; it alerts when a booking still held
personal data ``BACKLOG_AGE`` after the event that started its retention (CLAUDE.md privacy rules).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.booking import Booking, BookingStatus
from app.domain.closure_retention import (
    ClosureRetention,
    ClosureRetentionOf,
    suspended_purge_moment,
)
from app.domain.job_plan import PURGE_AFTER, JobType, PlannedJob
from app.domain.scheduling import GroupSummary
from app.domain.service_requests import ServiceRequest, data_deletion_at

PURGE_GRACE = timedelta(hours=1)
"""How long after its purge moment a booking may still hold personal data (worker, retries)."""

BACKLOG_AGE = timedelta(hours=48)
"""A booking older than this, counted from the event that started its retention, still holding
personal data is a backlog: the sweep alerts (fixed privacy rule, not a setting)."""


@dataclass(frozen=True)
class GroupRetentionCounts:
    """What the database counts for one group: no booking ids, no contents.

    Attributes:
        group: The group with its organisation.
        holding: Bookings that still hold personal data.
        failed_purges: ``purge_pii`` jobs that failed after their last attempt.
        last_purged_at: When the last booking of the group was purged, if ever.
    """

    group: GroupSummary
    holding: int
    failed_purges: int
    last_purged_at: datetime | None


@dataclass(frozen=True)
class PurgeCandidate:
    """A booking with personal data that may be past its purge moment (its times only).

    Attributes:
        group_id: The booking's group.
        status: The booking's status.
        end: The end of its slot.
        cancelled_at: When it was cancelled, if it was.
        closure_ends_at: For a suspended booking, the end of its closure (``None`` while open).
        rebook_by: For a booking awaiting a new time, when the request lapses (ADR 0044).
        confirm_by: For a booking awaiting confirmation, when it lapses (ADR 0056).
        organisation_id: The group's organisation, whose settings decide the maximum for a
            suspended booking (ADR 0102).
        closure_id: For a suspended booking, its closure.
        created_at: When it was made; a refused booking is purged a day after (ADR 0066).
    """

    group_id: int
    status: BookingStatus
    end: datetime
    cancelled_at: datetime | None
    closure_ends_at: datetime | None
    rebook_by: datetime | None = None
    confirm_by: datetime | None = None
    organisation_id: int = 0
    closure_id: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class DueBooking:
    """A purge candidate with its booking id, for the sweep (never for the overview).

    Attributes:
        booking_id: The booking that may be overdue.
        candidate: Its times and status.
    """

    booking_id: int
    candidate: PurgeCandidate


@dataclass(frozen=True)
class RetentionFacts:
    """What the adapter returns: the counts per group and the candidates to classify."""

    groups: list[GroupRetentionCounts]
    candidates: list[PurgeCandidate]


@dataclass(frozen=True)
class GroupRetention:
    """The overview of one group."""

    group: GroupSummary
    holding: int
    overdue: int
    failed_purges: int
    last_purged_at: datetime | None
    requests_holding: int = 0
    """Requests without an appointment whose request-level record or pseudonym is still held
    (ADR 0121 step 7, follow-up 5d): counted on their own, never as bookings."""
    requests_overdue: int = 0
    """Of those, how many are past their deletion moment and the grace."""

    @property
    def needs_attention(self) -> bool:
        """Whether personal data is overdue or a purge failed."""
        return self.overdue > 0 or self.failed_purges > 0 or self.requests_overdue > 0


@dataclass(frozen=True)
class RetentionOverview:
    """The overview of every group in scope, at ``checked_at``."""

    checked_at: datetime
    groups: tuple[GroupRetention, ...]

    @property
    def overdue(self) -> int:
        """Bookings past their purge moment, over all groups (the purge backlog)."""
        return sum(g.overdue for g in self.groups)

    @property
    def requests_overdue(self) -> int:
        """Request-level records past their deletion moment, over all groups (step 7)."""
        return sum(g.requests_overdue for g in self.groups)

    @property
    def failed_purges(self) -> int:
        """Failed purge jobs, over all groups."""
        return sum(g.failed_purges for g in self.groups)

    @property
    def last_purged_at(self) -> datetime | None:
        """The most recent purge in scope, if any."""
        return max((g.last_purged_at for g in self.groups if g.last_purged_at), default=None)

    @property
    def in_order(self) -> bool:
        """Whether nothing is overdue and no purge failed."""
        return not any(g.needs_attention for g in self.groups)


def purge_moment(candidate: PurgeCandidate, retention: ClosureRetention) -> datetime:
    """Return when the booking's personal data must go.

    ``retention`` is the maximum for a booking on hold (ADR 0102): an open closure no longer
    leaves the booking without a moment.
    """
    if candidate.status is BookingStatus.CANCELLED and candidate.cancelled_at is not None:
        return candidate.cancelled_at + PURGE_AFTER
    if candidate.status is BookingStatus.REFUSED and candidate.created_at is not None:
        return candidate.created_at + PURGE_AFTER
    if candidate.status is BookingStatus.SUSPENDED:
        return suspended_purge_moment(candidate.end, candidate.closure_ends_at, retention)
    if candidate.status is BookingStatus.AWAITING_REBOOK and candidate.rebook_by is not None:
        return candidate.rebook_by + PURGE_AFTER
    if candidate.status is BookingStatus.PENDING_CONFIRMATION and candidate.confirm_by is not None:
        return candidate.confirm_by + PURGE_AFTER
    return candidate.end + PURGE_AFTER


def candidates_before(now: datetime) -> datetime:
    """Return the moment before which a slot end or cancellation makes a booking a candidate.

    The adapter returns bookings with personal data whose slot ended or that were cancelled
    before it, every suspended one and every one awaiting a new time; ``summarise`` then
    applies ``purge_moment``.
    """
    return now - PURGE_AFTER - PURGE_GRACE


def is_overdue(candidate: PurgeCandidate, now: datetime, retention: ClosureRetention) -> bool:
    """Return whether the booking still holds personal data past its purge moment and grace."""
    return purge_moment(candidate, retention) + PURGE_GRACE < now


def is_backlog(candidate: PurgeCandidate, now: datetime, retention: ClosureRetention) -> bool:
    """Return whether the booking held personal data ``BACKLOG_AGE`` after retention began.

    Retention begins ``PURGE_AFTER`` before the purge moment (slot end, cancellation, lapse), so
    this is the case once the purge moment lies ``BACKLOG_AGE - PURGE_AFTER`` in the past.
    """
    return purge_moment(candidate, retention) + (BACKLOG_AGE - PURGE_AFTER) < now


def is_record_overdue(request: ServiceRequest, now: datetime) -> bool:
    """Return whether a request-level record is past its deletion moment and the grace.

    The one rule of the nightly sweep and of the overview (ADR 0121 steps 5 and 7).
    """
    moment = data_deletion_at(request)
    return moment is not None and moment + PURGE_GRACE < now


def summarise(
    facts: RetentionFacts,
    now: datetime,
    retention_of: ClosureRetentionOf,
    records: Iterable[ServiceRequest] = (),
) -> RetentionOverview:
    """Combine the counts and the overdue candidates into the overview, per group.

    ``retention_of`` gives the maximum of the organisation a candidate belongs to (ADR 0102).
    ``records`` are the requests without an appointment that still hold personal data
    (ADR 0121 step 7, follow-up 5d); a group outside the overview's scope is not counted.
    """
    overdue: dict[int, int] = {}
    for candidate in facts.candidates:
        if is_overdue(candidate, now, retention_of(candidate.organisation_id)):
            overdue[candidate.group_id] = overdue.get(candidate.group_id, 0) + 1
    held: dict[int, int] = {}
    late: dict[int, int] = {}
    for request in records:
        held[request.group_id] = held.get(request.group_id, 0) + 1
        if is_record_overdue(request, now):
            late[request.group_id] = late.get(request.group_id, 0) + 1
    return RetentionOverview(
        now,
        tuple(
            GroupRetention(
                counts.group,
                counts.holding,
                overdue.get(counts.group.group.id or 0, 0),
                counts.failed_purges,
                counts.last_purged_at,
                held.get(counts.group.group.id or 0, 0),
                late.get(counts.group.group.id or 0, 0),
            )
            for counts in facts.groups
        ),
    )


def jobs_after_purge(booking: Booking | None, now: datetime) -> list[PlannedJob]:
    """Return the jobs a purge of ``booking`` adds, decided on the booking *before* the purge.

    A guest was minted for the booking at the video platform (``guest_minted_at``) and the
    booking still holds its contact, so it was not purged before: one ``rename_guest`` job, which
    mints the guest again under the neutral word (ADR 0123 point 7). Nothing else follows a
    purge. A booking purged twice, or one without a guest, adds nothing.

    Args:
        booking: The booking as it is before the purge; ``None`` when it does not exist.
        now: The moment of the purge.
    """
    if booking is None or booking.guest_minted_at is None or booking.contact is None:
        return []
    return [PlannedJob(JobType.RENAME_GUEST, now, None)]
