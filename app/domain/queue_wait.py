"""The queue watches of the worker: waits too long (OV-7) and bookings not picked up (ADR 0043).

**Wait watch.**
The worker measures every own queue each loop. A group whose longest wait goes above its
organisation's ``queue_wait_warning_minutes`` gets one system event ``queue.wait`` (failure)
at the group, which is the alert (``app.domain.alerts``); going back under it records one
success, which ends the alert. A steady state records nothing, so a busy afternoon is one
alert, not one per loop.

    every own queue ─► longest wait > threshold? ─► changed since last loop? ─► system event

The watch remembers the groups it reported; after a restart it reads the open ``queue.wait``
alerts first, so a queue that emptied while the worker was down is closed.

Only a duration and a slug leave: never who waits.

**Not-picked-up watch** (ADR 0043 point 8). A booking in an own queue that nobody claimed
``not_picked_up_minutes`` after its start gets one system event ``queue.not_picked_up`` at the
booking (so at its group). ``CallStatus.not_picked_up_at`` is set in the same locked transaction
that finds it, so the event is recorded once, also across restarts. The event carries the
minutes the group allows, never who waits.

    every own queue ─► unclaimed past its limit, not reported yet? ─► mark ─► system event
"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta

from app.domain.alerts import QUEUE_WAIT_EVENT, QUEUE_WAIT_OVER
from app.domain.audit import Outcome
from app.domain.authorization import Scope
from app.domain.own_queue import not_claimed_in_time, summarise
from app.domain.ports import Clock, UnitOfWork
from app.domain.queue_service import load_entries
from app.domain.scheduling import GroupSummary
from app.domain.system_log import SystemLog

MINUTE = timedelta(minutes=1)
NOT_PICKED_UP_EVENT = "queue.not_picked_up"
"""The system event of a booking nobody claimed in time (ADR 0043 point 8)."""
NOT_PICKED_UP_REASON = "not_picked_up"


class QueueWaitWatch:
    """Records the crossings of the wait threshold, per group.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        system_log: Where the events go.
        threshold: The warning threshold of an organisation (by slug).
        reported: Returns the group slugs with an open ``queue.wait`` alert, read once at start.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        system_log: SystemLog,
        threshold: Callable[[str], timedelta],
        reported: Callable[[], Awaitable[set[str]]],
    ) -> None:
        """Start without memory; the first ``check`` reads the open alerts."""
        self._uow = uow
        self._clock = clock
        self._system_log = system_log
        self._threshold = threshold
        self._reported = reported
        self._over: set[str] | None = None

    async def check(self) -> list[str]:
        """Measure every own queue and record each crossing; return the slugs that crossed."""
        if self._over is None:
            self._over = await self._reported()
        over = self._over
        now = self._clock.now()
        everything = Scope(everything=True)
        async with self._uow.read() as tx:
            groups = [g for g in await tx.list_groups(everything) if g.group.queue_open]
            waits = [
                (g, summarise(await load_entries(tx, everything, g.group, now), now).longest_wait)
                for g in groups
            ]
        crossed = []
        for summary, wait in waits:
            if await self._observe(over, summary, wait):
                crossed.append(summary.group.slug)
        # A queue switched off or a group erased keeps no memory here.
        over.intersection_update(g.group.slug for g in groups)
        return crossed

    async def _observe(self, over: set[str], summary: GroupSummary, wait: timedelta) -> bool:
        """Record the event of one group if it crossed the threshold either way; update ``over``."""
        slug = summary.group.slug
        above = wait > self._threshold(summary.organisation_slug)
        if above == (slug in over):
            return False
        await self._system_log.record(
            QUEUE_WAIT_EVENT,
            Outcome.FAILURE if above else Outcome.SUCCESS,
            group_slug=slug,
            reason=QUEUE_WAIT_OVER if above else "",
            detail=f"minutes:{int(wait / MINUTE)}",
        )
        if above:
            over.add(slug)
        else:
            over.discard(slug)
        return True


class NotPickedUpWatch:
    """Records each own-queue booking nobody claimed in time, once (ADR 0043 point 8).

    Args:
        uow: Opens database transactions.
        clock: Current time.
        system_log: Where the events go.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, system_log: SystemLog) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._system_log = system_log

    async def check(self) -> int:
        """Mark and record every booking newly not picked up; return how many."""
        now = self._clock.now()
        everything = Scope(everything=True)
        found: list[tuple[int, int]] = []
        async with self._uow.locked() as tx:
            for summary in await tx.list_groups(everything):
                group = summary.group
                if not group.queue_open:
                    continue
                limit = timedelta(minutes=group.not_picked_up_minutes)
                for entry in await load_entries(tx, everything, group, now):
                    booking_id = entry.booking.id
                    if (
                        booking_id is None
                        or entry.status.not_picked_up_at is not None
                        or entry.status.host_joined
                        or not not_claimed_in_time(entry, now, limit)
                    ):
                        continue
                    status = replace(entry.status, not_picked_up_at=now)
                    await tx.save_call_status(status)
                    found.append((booking_id, group.not_picked_up_minutes))
        # Outside the write lock: the system log writes in a transaction of its own. A crash in
        # between leaves a booking marked without its event; it is never recorded twice.
        for booking_id, minutes in found:
            await self._system_log.record(
                NOT_PICKED_UP_EVENT,
                Outcome.FAILURE,
                booking_id=booking_id,
                reason=NOT_PICKED_UP_REASON,
                detail=f"minutes:{minutes}",
            )
        return len(found)
