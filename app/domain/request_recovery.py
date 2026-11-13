"""Work in recovery: the supervisors' list and the alert while any exists (ADR 0121 step 5e).

The watchdog marks started work that is still not completed at its deadline
``recovery_required`` (ADR 0121 point 7). It keeps one unit of its pool occupied as an overrun
until someone completes it (A34), which refuses conflicting starts and shows as a shortage. Its
appointment has ended, so the queue, which lists bookings until their end, no longer shows it
(design R9). Two things keep it in sight:

    QueueService.view(principal, slug).recovery
        ─► for a supervisor of the queue (bookings.view, as ``release``): every request of the
           service in recovery (``recovery_of``), its reference, start, planned end and
           deadline; *Complete* goes through the queue's own routes, which let a supervisor
           complete work in recovery
    RecoveryWatch.check()
        ─► per service: work in recovery now, and not reported yet ─► one system event
           ``requests.recovery`` (failure, the alert); none any more ─► one success

Counts, references and times only: never who the participant or the agent is.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from app.domain.alerts import RECOVERY_EVENT, RECOVERY_REASON
from app.domain.authorization import Scope
from app.domain.condition_memory import GroupConditions
from app.domain.ports import Transaction, UnitOfWork
from app.domain.system_log import SystemLog


@dataclass(frozen=True)
class RecoveryWork:
    """One request in recovery, as a supervisor's list shows it: no name, no contact.

    Attributes:
        request_id: The request.
        booking_id: Its appointment, ``None`` for work without one.
        started_at: When treatment started (UTC); ``None`` only for a state the watchdog never
            writes.
        planned_until: The planned end of its occupation of the pool.
        watchdog_at: The deadline that marked it for recovery.
    """

    request_id: int
    booking_id: int | None
    started_at: datetime | None
    planned_until: datetime | None
    watchdog_at: datetime

    @property
    def reference(self) -> str | None:
        """The appointment's reference (``OP-000123``), ``None`` for work without one."""
        return None if self.booking_id is None else f"OP-{self.booking_id:06d}"


async def recovery_of(tx: Transaction, group_id: int) -> list[RecoveryWork]:
    """Return the requests of service ``group_id`` in recovery, oldest deadline first."""
    found = []
    for request in await tx.requests_in_recovery(group_id):
        if request.id is None:  # pragma: no cover - a stored request has an id
            continue
        item = await tx.get_active_work_item(request.id)
        found.append(
            RecoveryWork(
                request.id,
                request.booking_id,
                item.started_at if item else None,
                item.planned_until if item else None,
                request.watchdog_at,
            )
        )
    return sorted(found, key=lambda w: (w.watchdog_at, w.request_id))


class RecoveryWatch:
    """Records, per service, when work in recovery starts to exist and when none is left.

    Args:
        uow: Opens database transactions.
        system_log: Where the events go.
        reported: Returns the service slugs with an open ``requests.recovery`` alert, read once
            at start, so a restarted worker closes an alert whose work was completed meanwhile.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        system_log: SystemLog,
        reported: Callable[[], Awaitable[set[str]]],
    ) -> None:
        """Start without memory; the first ``check`` reads the open alerts."""
        self._uow = uow
        self._conditions = GroupConditions(system_log, RECOVERY_EVENT, RECOVERY_REASON, reported)

    async def check(self) -> list[str]:
        """Count every service's work in recovery and record each change; return those slugs."""
        everything = Scope(everything=True)
        async with self._uow.read() as tx:
            counts: dict[int, int] = {}
            for request in await tx.requests_in_recovery(None):
                counts[request.group_id] = counts.get(request.group_id, 0) + 1
            groups = await tx.list_groups(everything)
        changed = []
        for summary in groups:
            count = counts.get(summary.group.id, 0)
            if await self._conditions.observe(summary.group.slug, count > 0, f"count:{count}"):
                changed.append(summary.group.slug)
        await self._conditions.keep(g.group.slug for g in groups)
        return changed
