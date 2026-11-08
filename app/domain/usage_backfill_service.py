"""Turns the bookings that existed before reporting into usage facts (ADR 0117 point 5).

    first run? ─► cutoff (the hour live facts start from, stored)
               ─► per batch of booking rows not yet done: facts ─► outbox ─► rows marked done
               ─► completed (recorded once)

Facts and marks share a transaction, so a row is never done twice or lost, and a second run
finds nothing left (idempotent). The rollup picks the facts up like any other. Nothing here
reads a purgeable column (see ``usage_backfill``). With ``reporting_enabled`` off nothing is
written and nothing is marked.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.domain.ports import Clock, UsageUnitOfWork
from app.domain.usage_backfill import BATCH_SIZE, facts_of


@dataclass(frozen=True)
class BackfillRun:
    """What one run did: counts only.

    Attributes:
        bookings: Booking rows looked at (and marked done).
        facts: Facts written.
        skipped: ``True`` when reporting is off and nothing was done.
    """

    bookings: int = 0
    facts: int = 0
    skipped: bool = False


class UsageBackfill:
    """Backfills usage facts from existing bookings.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        enabled: Whether reporting is on (``reporting_enabled``, read at each run).
    """

    def __init__(self, uow: UsageUnitOfWork, clock: Clock, enabled: Callable[[], bool]) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._enabled = enabled

    async def run_once(self) -> BackfillRun:
        """Run unless a run completed before: the worker's automatic first run."""
        async with self._uow.read() as tx:
            if (await tx.usage_state()).backfilled_at is not None:
                return BackfillRun()
        return await self.run()

    async def run(self) -> BackfillRun:
        """Back-fill every booking row not yet done, one batch per transaction."""
        if not self._enabled():
            return BackfillRun(skipped=True)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            cutoff = await tx.usage_cutoff(now)
        bookings = facts = 0
        after_id = 0
        while True:
            async with self._uow.locked() as tx:
                found = await tx.find_unbackfilled(after_id, BATCH_SIZE)
                written = 0
                for booking in found:
                    for fact in facts_of(booking, cutoff):
                        written += int(await tx.record_usage(fact))
                await tx.mark_backfilled(
                    [b.id for b in found], None if len(found) == BATCH_SIZE else now
                )
            bookings += len(found)
            facts += written
            if len(found) < BATCH_SIZE:
                break
            after_id = found[-1].id
        return BackfillRun(bookings, facts)
