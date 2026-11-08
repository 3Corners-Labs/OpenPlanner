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
from app.domain.usage_backfill import BATCH_SIZE, MAX_BATCHES, facts_of


@dataclass(frozen=True)
class BackfillRun:
    """What one run did: counts only.

    Attributes:
        bookings: Booking rows looked at (and marked done).
        facts: Facts written.
        skipped: ``True`` when reporting is off and nothing was done.
        complete: Whether every booking has now been looked at.
    """

    bookings: int = 0
    facts: int = 0
    skipped: bool = False
    complete: bool = False


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
        self._finished = False

    async def run_once(self) -> BackfillRun:
        """Take up the backfill for one bounded pass, until it is complete.

        The worker calls this on every pass: it does nothing once a run completed (known in
        memory after the first check), and otherwise does at most ``MAX_BATCHES`` batches, so
        a long booking history never keeps the worker from its jobs.
        """
        if self._finished:
            return BackfillRun(complete=True)
        async with self._uow.read() as tx:
            if (await tx.usage_state()).backfilled_at is not None:
                self._finished = True
                return BackfillRun(complete=True)
        run = await self.run(MAX_BATCHES)
        self._finished = run.complete
        return run

    async def run(self, max_batches: int | None = None) -> BackfillRun:
        """Back-fill booking rows not yet done, one batch per transaction.

        ``max_batches`` bounds the run (the worker); without it the run goes to the end (the CLI).
        """
        if not self._enabled():
            return BackfillRun(skipped=True)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            cutoff = await tx.usage_cutoff(now)
        bookings = facts = 0
        after_id = 0
        batches = 0
        while max_batches is None or batches < max_batches:
            batches += 1
            async with self._uow.locked() as tx:
                found = await tx.find_unbackfilled(after_id, BATCH_SIZE)
                written = 0
                for booking in found:
                    for fact in facts_of(booking, cutoff):
                        written += int(await tx.record_usage(fact))
                await tx.mark_backfilled([b.id for b in found])
                complete = len(found) < BATCH_SIZE
                if complete:
                    await tx.complete_backfill(now)
            bookings += len(found)
            facts += written
            if complete:
                return BackfillRun(bookings, facts, complete=True)
            after_id = found[-1].id
        return BackfillRun(bookings, facts)
