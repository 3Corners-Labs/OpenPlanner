"""The worker's calls into the usage rollup and backfill: they log, and never stop the loop.

The domain services (``usage_rollup_service``, ``usage_backfill_service``) raise on failure and
do not log; this module is where the worker turns a failure into a log line that names the
error type only and goes on with the next interval (ADR 0117 point 4). Only counts are logged.
"""

import structlog

from app.domain.slot_close_service import SlotCloser
from app.domain.usage_backfill_service import UsageBackfill
from app.domain.usage_rollup_service import RollupRun, UsageRollup

log = structlog.get_logger()


async def roll_up_usage(rollup: UsageRollup) -> RollupRun | None:
    """Run the rollup if it is due; return what it did, or ``None`` when not due or failed."""
    try:
        run = await rollup.run_if_due()
    except Exception as exc:  # never stop the worker loop over reporting
        log.error("usage.rollup_failed", error=type(exc).__name__)
        return None
    if run is not None and (run.facts or run.unreadable or run.pruned.events or run.pruned.hours):
        log.info(
            "usage.rolled_up",
            facts=run.facts,
            cells=run.cells,
            unreadable=run.unreadable,
            events_pruned=run.pruned.events,
            hours_pruned=run.pruned.hours,
        )
    return run


async def backfill_usage(backfill: UsageBackfill) -> None:
    """Run the backfill once after the upgrade; a failure is logged, never fatal."""
    try:
        run = await backfill.run_once()
    except Exception as exc:  # e.g. schema not migrated yet; the next start tries again
        log.warning("usage.backfill_failed", error=type(exc).__name__)
        return
    if run.bookings:
        log.info("usage.backfilled", bookings=run.bookings, facts=run.facts, complete=run.complete)


async def close_slots(closer: SlotCloser) -> None:
    """Close the slots that ended if it is due; a failure is logged, never fatal (ADR 0117)."""
    try:
        run = await closer.run_if_due()
    except Exception as exc:  # never stop the worker loop over reporting
        log.error("usage.slots_failed", error=type(exc).__name__)
        return
    if run is not None and run.slots:
        log.info("usage.slots_closed", slots=run.slots, facts=run.facts)
