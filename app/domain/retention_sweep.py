"""The nightly retention sweep: purge personal data the ``purge_pii`` job missed (WP-A1).

The ``purge_pii`` job is the normal way personal data goes. If it never ran, ran out of retries
or was never planned (a bug, a restored backup), the booking would keep its data for ever. The
sweep is the safety net under it, and it must not depend on the mechanism that failed, so it
purges directly instead of planning another job:

    list candidates ─► overdue? (``is_overdue``: purge moment + grace passed)
                    ─► per booking, in its own locked transaction:
                         still the same booking state? ─► purge ─► drop its pending jobs
                    ─► system event ``retention.sweep`` (counts only)

It is idempotent: a purged booking is no candidate any more. A booking under an open-ended
closure has no purge moment and is left alone until WP-A2 decides on a maximum. Events carry
counts, never names, addresses or booking contents.
"""

from dataclasses import dataclass, field

from app.domain.audit import Outcome
from app.domain.job_plan import JobType
from app.domain.ports import Clock, UnitOfWork
from app.domain.retention import DueBooking, candidates_before, is_backlog, is_overdue
from app.domain.system_log import SystemLog

_PURGED = "purged"
_CHANGED = "changed"

SWEPT = "retention.sweep"
"""The system event written when the sweep found something overdue."""


@dataclass(frozen=True)
class SweepResult:
    """What one sweep found and did.

    Attributes:
        overdue: Bookings that held personal data past their purge moment plus grace.
        purged: Of those, how many were purged now (0 for a dry run).
        failed: Of those, how many could not be purged (they are tried again next time).
        backlog: Of those, how many were past ``BACKLOG_AGE``: the alert condition.
        per_group: Overdue bookings per group slug.
    """

    overdue: int = 0
    purged: int = 0
    failed: int = 0
    backlog: int = 0
    per_group: dict[str, int] = field(default_factory=dict)


class RetentionSweep:
    """Purges overdue bookings directly and records the outcome.

    Args:
        uow: Opens database transactions.
        clock: Current time.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._events = SystemLog(uow, clock)

    async def run(self, *, dry_run: bool = False) -> SweepResult:
        """Purge every overdue booking; with ``dry_run`` only count them per group."""
        now = self._clock.now()
        async with self._uow.read() as tx:
            found = await tx.due_for_purge(candidates_before(now))
            overdue = [d for d in found if is_overdue(d.candidate, now)]
            per_group: dict[str, int] = {}
            for due in overdue:
                group = await tx.get_group_by_id(due.candidate.group_id)
                slug = group.slug if group is not None else "unknown"
                per_group[slug] = per_group.get(slug, 0) + 1
        backlog = sum(1 for d in overdue if is_backlog(d.candidate, now))
        if dry_run or not overdue:
            return SweepResult(len(overdue), 0, 0, backlog, per_group)
        outcomes = [await self._purge(due) for due in overdue]
        purged = outcomes.count(_PURGED)
        errors = sorted({o for o in outcomes if o not in (_PURGED, _CHANGED)})
        failed = sum(1 for o in outcomes if o in errors)
        result = SweepResult(len(overdue), purged, failed, backlog, per_group)
        await self._events.record(
            SWEPT,
            Outcome.FAILURE if failed else Outcome.SUCCESS,
            reason=",".join(errors),
            detail=f"overdue:{result.overdue},purged:{purged},failed:{failed},backlog:{backlog}",
        )
        return result

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
