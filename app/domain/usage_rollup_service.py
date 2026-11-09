"""The worker's rollup of usage facts into the hourly table, and its retention (ADR 0117 point 4).

    due? ─► read facts after the watermark (a fixed batch) ─► cells ─► add to ``usage_hour``
         ─► move the watermark                                (one transaction per batch)
         ─► prune: rolled-up facts past ``reporting_event_retention_days``, hourly rows past
            ``reporting_retention_months``

The cells and the watermark share a transaction, so a crash neither double-counts nor loses a
fact (idempotent: a second run finds nothing after the watermark). A fact written while the
rollup runs is picked up by the next run. A failure propagates to the worker's wrapper
(``app.jobs.usage``), which logs the error type and goes on; the next interval tries again.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.ports import Clock, UsageUnitOfWork
from app.domain.usage_rollup import (
    BATCH_SIZE,
    MAX_BATCHES,
    PRUNE_BATCH,
    UsagePruned,
    events_cutoff,
    hours_cutoff,
    roll,
)
from app.domain.usage_staff import roll_staff


@dataclass(frozen=True)
class RollupRun:
    """What one run did: counts only.

    Attributes:
        facts: Facts rolled up.
        cells: Distinct hourly cells they were added to.
        unreadable: Rows of a newer version that were passed over.
        pruned: What retention removed.
        more: Whether facts or rows past their term are left for the next pass.
    """

    facts: int = 0
    cells: int = 0
    unreadable: int = 0
    pruned: UsagePruned = UsagePruned()
    more: bool = False


class UsageRollup:
    """Rolls usage facts up, at most once per interval, and prunes what retention allows.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        interval: ``reporting_rollup_interval_minutes`` as a period (class C, read each time).
        event_retention_days: ``reporting_event_retention_days`` (class C, read each time).
        retention_months: ``reporting_retention_months`` (class C, read each time).
    """

    def __init__(
        self,
        uow: UsageUnitOfWork,
        clock: Clock,
        interval: Callable[[], timedelta],
        event_retention_days: Callable[[], int],
        retention_months: Callable[[], int],
    ) -> None:
        """Store the collaborators; nothing has run yet."""
        self._uow = uow
        self._clock = clock
        self._interval = interval
        self._event_days = event_retention_days
        self._months = retention_months
        self._ran_at: datetime | None = None

    def due(self, now: datetime) -> bool:
        """Return whether a run is due: never ran, or the interval has passed."""
        return self._ran_at is None or now - self._ran_at >= self._interval()

    async def run_if_due(self) -> RollupRun | None:
        """Run when due and return what it did; ``None`` when not due.

        The interval counts from the attempt, so a failing run is not retried before the next
        interval; a run that left work (``more``) is due again at the next pass. Any failure
        propagates to the caller.
        """
        now = self._clock.now()
        if not self.due(now):
            return None
        self._ran_at = now
        result = await self.run(now)
        if result.more:
            self._ran_at = None
        return result

    async def run(self, now: datetime) -> RollupRun:
        """Roll up facts after the watermark, a bounded number of batches, then prune a batch.

        Each batch is one transaction. Whatever a run leaves (``more``) the next pass takes up.
        """
        facts = cells = unreadable = 0
        drained = False
        for _ in range(MAX_BATCHES):
            async with self._uow.locked() as tx:
                state = await tx.usage_state()
                batch = await tx.read_usage_events(state.last_event_id, BATCH_SIZE)
                merged = roll(batch.facts)
                await tx.add_usage_cells(merged)
                await tx.add_staff_cells(roll_staff(batch.facts))
                await tx.advance_usage_watermark(batch.last_id, now)
            facts += len(batch.facts)
            cells += len(merged)
            unreadable += batch.unreadable
            if len(batch.facts) + batch.unreadable < BATCH_SIZE:  # a short batch is the last
                drained = True
                break
        async with self._uow.locked() as tx:
            pruned = await tx.prune_usage(
                events_cutoff(now, self._event_days()),
                hours_cutoff(now, self._months()),
                PRUNE_BATCH,
            )
        more = not drained or pruned.events >= PRUNE_BATCH or pruned.hours >= PRUNE_BATCH
        return RollupRun(facts, cells, unreadable, pruned, more)
