"""Merging old booking rows into daily counts (ADR 0102 point 12, step 4).

After the purge a booking row holds no personal data any more, but its times and reference
could still point to a person together with data held elsewhere. The row goes
``booking_row_retention_days`` after ``pii_purged_at``; what statistics need survives as a
count per group, day and final status:

    list rollable ─► row still holds data or an open job? ─► left alone, tried again later
                  ─► count per (group, day, status) ─► add to the day counts
                  ─► delete the rows, their events and call status   (one transaction per batch)
                  ─► one system event ``booking_rows.rollup`` (counts only)

The merge and the delete share a transaction, so a row is never counted twice or lost, and a
second run finds nothing left to do (idempotent).
"""

from collections.abc import Callable
from datetime import timedelta

from app.domain.audit import Outcome
from app.domain.booking_rollup import (
    BATCH_SIZE,
    RollupResult,
    is_rollable,
    purged_before,
    tally,
)
from app.domain.ports import Clock, UnitOfWork
from app.domain.system_log import SystemLog

ROLLED_UP = "booking_rows.rollup"
"""The system event written when a run merged and deleted rows (counts only)."""


class BookingRowRollup:
    """Merges old purged booking rows into daily counts and deletes them.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        retention: ``booking_row_retention_days`` as a period (class C, read at every run).
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, retention: Callable[[], timedelta]) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._retention = retention
        self._events = SystemLog(uow, clock)

    async def run(self) -> RollupResult:
        """Roll up every row past the term, one batch per transaction; one event if it did."""
        now = self._clock.now()
        retention = self._retention()
        rows = days = skipped = 0
        after_id = 0
        while True:
            async with self._uow.locked() as tx:
                found = await tx.list_rollable(purged_before(now, retention), after_id, BATCH_SIZE)
                ready = [r for r in found if is_rollable(r, now, retention)]
                if ready:
                    counts = tally(ready)
                    await tx.add_day_counts(counts)
                    await tx.delete_booking_rows([r.booking_id for r in ready])
                    days += len(counts)
            if not found:
                break
            rows += len(ready)
            skipped += len(found) - len(ready)
            after_id = max(r.booking_id for r in found)
        result = RollupResult(rows, days, skipped)
        if rows or skipped:
            await self._events.record(
                ROLLED_UP,
                Outcome.SUCCESS,
                detail=f"rows:{rows},days:{days},skipped:{skipped}",
            )
        return result
