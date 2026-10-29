"""Retention overview: whether personal data is removed on time (ADR 0039, ``retention.view``).

Per group in scope it counts the bookings that still hold personal data, how many of them
are past their purge moment, and how many ``purge_pii`` jobs failed, plus when the last purge
ran. Counts and times only: nothing here identifies a booking or contains its contents, so
the auditor role can see it.

A booking's purge moment follows the privacy rules (CLAUDE.md, ADR 0024):

    confirmed / completed / no show / direct call ─► slot end + 24 h
    cancelled                                      ─► cancellation + 24 h
    suspended                                      ─► closure end + 30 days (none while open)
    awaiting a new time (ADR 0044)                 ─► rebook_by + 24 h (it lapses then)

A booking counts as overdue once its purge moment lies more than ``PURGE_GRACE`` in the past,
so a purge that is waiting for the worker or for a retry does not raise an alarm.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.booking import BookingStatus
from app.domain.closure import RETENTION_AFTER_CLOSURE
from app.domain.job_plan import PURGE_AFTER
from app.domain.scheduling import GroupSummary

PURGE_GRACE = timedelta(hours=1)
"""How long after its purge moment a booking may still hold personal data (worker, retries)."""


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
    """

    group_id: int
    status: BookingStatus
    end: datetime
    cancelled_at: datetime | None
    closure_ends_at: datetime | None
    rebook_by: datetime | None = None


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

    @property
    def needs_attention(self) -> bool:
        """Whether personal data is overdue or a purge failed."""
        return self.overdue > 0 or self.failed_purges > 0


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


def purge_moment(candidate: PurgeCandidate) -> datetime | None:
    """Return when the booking's personal data must go; ``None`` while a closure is open."""
    if candidate.status is BookingStatus.CANCELLED and candidate.cancelled_at is not None:
        return candidate.cancelled_at + PURGE_AFTER
    if candidate.status is BookingStatus.SUSPENDED:
        if candidate.closure_ends_at is None:
            return None
        return candidate.closure_ends_at + RETENTION_AFTER_CLOSURE
    if candidate.status is BookingStatus.AWAITING_REBOOK and candidate.rebook_by is not None:
        return candidate.rebook_by + PURGE_AFTER
    return candidate.end + PURGE_AFTER


def candidates_before(now: datetime) -> datetime:
    """Return the moment before which a slot end or cancellation makes a booking a candidate.

    The adapter returns bookings with personal data whose slot ended or that were cancelled
    before it, every suspended one and every one awaiting a new time; ``summarise`` then
    applies ``purge_moment``.
    """
    return now - PURGE_AFTER - PURGE_GRACE


def is_overdue(candidate: PurgeCandidate, now: datetime) -> bool:
    """Return whether the booking still holds personal data past its purge moment and grace."""
    moment = purge_moment(candidate)
    return moment is not None and moment + PURGE_GRACE < now


def summarise(facts: RetentionFacts, now: datetime) -> RetentionOverview:
    """Combine the counts and the overdue candidates into the overview, per group."""
    overdue: dict[int, int] = {}
    for candidate in facts.candidates:
        if is_overdue(candidate, now):
            overdue[candidate.group_id] = overdue.get(candidate.group_id, 0) + 1
    return RetentionOverview(
        now,
        tuple(
            GroupRetention(
                counts.group,
                counts.holding,
                overdue.get(counts.group.group.id or 0, 0),
                counts.failed_purges,
                counts.last_purged_at,
            )
            for counts in facts.groups
        ),
    )
