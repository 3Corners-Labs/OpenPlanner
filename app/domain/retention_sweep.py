"""The nightly retention sweep: purge personal data the ``purge_pii`` job missed (WP-A1).

The ``purge_pii`` job is the normal way personal data goes. If it never ran, ran out of retries
or was never planned (a bug, a restored backup), the booking would keep its data for ever. The
sweep is the safety net under it, and it must not depend on the mechanism that failed, so it
purges directly instead of planning another job:

    list candidates ─► overdue? (``is_overdue``: purge moment + grace passed)
                    ─► per booking, in its own locked transaction:
                         still the same booking state? ─► purge ─► drop its pending jobs
                    ─► system event ``retention.sweep`` (counts only)

The request-level records of requests without an appointment (ADR 0121 decision D1) are swept
the same way: one past ``data_deletion_at`` plus the grace is deleted and counted with the
bookings (overdue, purged, backlog), so the alert covers both. The watchdog round deletes them on
time; the sweep is the net under it.

It is idempotent: a purged booking is no candidate any more. A booking under an open-ended
closure is purged at the maximum after its original slot (``closure_max_retention_days``,
ADR 0102), the same moment the job plan and the overview use. Before that moment the sweep tells
the group's administrators, ``closure_max_notice_days`` ahead, that a closure still holds data
and on what date it goes (system event ``closure.retention_notice``: counts and a date).
Events carry counts, never names, addresses or booking contents.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.audit import Outcome
from app.domain.booking import BookingStatus
from app.domain.closure_retention import (
    ClosureRetentionOf,
    default_closure_retention,
    is_capped,
)
from app.domain.job_plan import PURGE_AFTER, JobType
from app.domain.ports import Clock, UnitOfWork
from app.domain.retention import (
    BACKLOG_AGE,
    PURGE_GRACE,
    DueBooking,
    candidates_before,
    is_backlog,
    is_overdue,
    purge_moment,
)
from app.domain.service_requests import ServiceRequest, data_deletion_at
from app.domain.system_log import SystemLog

_PURGED = "purged"
_CHANGED = "changed"

SWEPT = "retention.sweep"
"""The system event written when the sweep found something overdue."""

CLOSURE_NOTICE = "closure.retention_notice"
"""The system event, at the group, that a closure still holds data until a date (ADR 0102)."""


@dataclass(frozen=True)
class SweepResult:
    """What one sweep found and did.

    Attributes:
        overdue: Bookings that held personal data past their purge moment plus grace.
        purged: Of those, how many were purged now (0 for a dry run).
        failed: Of those, how many could not be purged (they are tried again next time).
        backlog: Of those, how many were past ``BACKLOG_AGE``: the alert condition.
        per_group: Overdue bookings per group slug.
        notices: Closures the administrators were told about (ADR 0102).
    """

    overdue: int = 0
    purged: int = 0
    failed: int = 0
    backlog: int = 0
    per_group: dict[str, int] = field(default_factory=dict)
    notices: int = 0


@dataclass(frozen=True)
class ClosureNotice:
    """A closure whose bookings reach the maximum soon: counts and a date, nothing else.

    Attributes:
        group_id: The closed group.
        closure_id: The closure.
        bookings: How many suspended bookings reach the maximum within the notice period.
        delete_by: The first date their personal data is deleted at the latest.
    """

    group_id: int
    closure_id: int
    bookings: int
    delete_by: datetime


def closure_notices(
    found: list[DueBooking], now: datetime, retention_of: ClosureRetentionOf
) -> list[ClosureNotice]:
    """Return the closures to warn about: the maximum, not the closure's end, is due soon.

    A suspended booking counts when the maximum decides its purge moment (``is_capped``), the
    moment lies ahead and is at most the organisation's notice period away.
    """
    grouped: dict[tuple[int, int], list[datetime]] = {}
    for due in found:
        candidate = due.candidate
        if candidate.status is not BookingStatus.SUSPENDED or candidate.closure_id is None:
            continue
        retention = retention_of(candidate.organisation_id)
        if not is_capped(candidate.end, candidate.closure_ends_at, retention):
            continue
        moment = purge_moment(candidate, retention)
        if now < moment <= now + retention.notice:
            grouped.setdefault((candidate.group_id, candidate.closure_id), []).append(moment)
    return [
        ClosureNotice(group_id, closure_id, len(moments), min(moments))
        for (group_id, closure_id), moments in sorted(grouped.items())
    ]


class RetentionSweep:
    """Purges overdue bookings directly and records the outcome.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        retention_of: The organisation's maximum for bookings on hold (ADR 0102, class D).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        retention_of: ClosureRetentionOf = default_closure_retention,
    ) -> None:
        """Store the collaborators; no closure has been announced yet."""
        self._uow = uow
        self._clock = clock
        self._retention_of = retention_of
        self._events = SystemLog(uow, clock)
        self._announced: set[tuple[int, int, date]] = set()

    async def run(self, *, dry_run: bool = False) -> SweepResult:
        """Purge every overdue booking and record; with ``dry_run`` only count them per group."""
        now = self._clock.now()
        async with self._uow.read() as tx:
            found = await tx.due_for_purge(candidates_before(now))
            overdue = [
                d
                for d in found
                if is_overdue(d.candidate, now, self._retention_of(d.candidate.organisation_id))
            ]
            records = [r for r in await tx.requests_holding_data() if _record_overdue(r, now)]
            per_group: dict[str, int] = {}
            for group_id in [d.candidate.group_id for d in overdue] + [r.group_id for r in records]:
                group = await tx.get_group_by_id(group_id)
                slug = group.slug if group is not None else "unknown"
                per_group[slug] = per_group.get(slug, 0) + 1
        backlog = sum(
            1
            for d in overdue
            if is_backlog(d.candidate, now, self._retention_of(d.candidate.organisation_id))
        ) + sum(1 for r in records if _record_backlog(r, now))
        total = len(overdue) + len(records)
        if dry_run:
            return SweepResult(total, 0, 0, backlog, per_group)
        notices = await self._announce(closure_notices(found, now, self._retention_of))
        if not total:
            return SweepResult(0, 0, 0, backlog, per_group, notices)
        outcomes = [await self._purge(due) for due in overdue]
        outcomes += [await self._delete_record(r.id or 0) for r in records]
        purged = outcomes.count(_PURGED)
        errors = sorted({o for o in outcomes if o not in (_PURGED, _CHANGED)})
        failed = sum(1 for o in outcomes if o in errors)
        result = SweepResult(total, purged, failed, backlog, per_group, notices)
        await self._events.record(
            SWEPT,
            Outcome.FAILURE if failed else Outcome.SUCCESS,
            reason=",".join(errors),
            detail=f"overdue:{result.overdue},purged:{purged},failed:{failed},backlog:{backlog}",
        )
        return result

    async def _announce(self, notices: list[ClosureNotice]) -> int:
        """Tell the group's administrators about each closure once per day; return how many.

        The event sits at the group, so its admins, the organisation's admins and the
        superuser see it; it carries the closure id, a count and a date. A closure is told
        again on a later day, which is the reminder; a restart of the process may repeat one.
        """
        told = 0
        for notice in notices:
            today = self._clock.now().date()
            self._announced = {k for k in self._announced if k[2] == today}
            key = (notice.group_id, notice.closure_id, today)
            if key in self._announced:
                continue
            async with self._uow.read() as tx:
                group = await tx.get_group_by_id(notice.group_id)
            if group is None:
                continue
            await self._events.record(
                CLOSURE_NOTICE,
                Outcome.SUCCESS,
                group_slug=group.slug,
                detail=(
                    f"closure:{notice.closure_id},bookings:{notice.bookings},"
                    f"delete_by:{notice.delete_by.date().isoformat()}"
                ),
            )
            self._announced.add(key)
            told += 1
        return told

    async def _purge(self, due: DueBooking) -> str:
        """Purge one booking if it is still in the state that made it a candidate.

        Returns ``_PURGED``, ``_CHANGED`` (it changed meanwhile and is left for the next sweep)
        or the type name of the error; a failure never stops the others.
        """
        try:
            async with self._uow.locked() as tx:
                booking = await tx.get_booking(due.booking_id)
                candidate = due.candidate
                if (
                    booking is None
                    or booking.status is not candidate.status
                    or booking.end != candidate.end
                    or booking.cancelled_at != candidate.cancelled_at
                ):
                    return _CHANGED
                await tx.purge_personal_data(due.booking_id, self._clock.now())
                await tx.delete_pending_jobs(due.booking_id, set(JobType))
        except Exception as exc:  # one booking must not stop the others; the event reports it
            return type(exc).__name__
        return _PURGED

    async def _delete_record(self, request_id: int) -> str:
        """Delete one overdue request-level record; ``_CHANGED`` when it went meanwhile."""
        try:
            async with self._uow.locked() as tx:
                deleted = await tx.delete_request_personal_data(request_id, self._clock.now())
        except Exception as exc:  # one record must not stop the others; the event reports it
            return type(exc).__name__
        return _PURGED if deleted else _CHANGED


def _record_overdue(request: ServiceRequest, now: datetime) -> bool:
    """Return whether a request-level record is past its deletion moment and the grace."""
    moment = data_deletion_at(request)
    return moment is not None and moment + PURGE_GRACE < now


def _record_backlog(request: ServiceRequest, now: datetime) -> bool:
    """Return whether a record held data ``BACKLOG_AGE`` after its retention began."""
    moment = data_deletion_at(request)
    return moment is not None and moment + (BACKLOG_AGE - PURGE_AFTER) < now
