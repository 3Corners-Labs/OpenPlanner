"""Worker housekeeping: watch the data volume and give freed database pages back (ADR 0040).

Called once per worker loop, after the due jobs:

    measure ─► grade ─► not ok? ─► log storage.degraded (on change, then hourly)
                     ─► changed? ─► system event storage.check (superuser's audit log)
    critical ─► brake: every diagnostic mode ends, so no debug events are written (part 4)
    hourly ─► PRAGMA incremental_vacuum ─► pages back to the file system
    daily ─► erase the archived groups nothing refers to any more (ADR 0084 step 3)
    hourly ─► delete ended and expired admin sessions (ADR 0098 step 2)
    daily ─► erase staff records without a login for ``staff_retention_days`` (ADR 0046 step 2)
    hourly ─► delete update back-ups older than a day, the second safety net (ADR 0102 point 10)
    daily ─► merge booking rows past booking_row_retention_days into daily counts and delete
             them (ADR 0102 step 4)
    daily ─► retention sweep: purge personal data the purge job missed, alert on a backlog
             ─► purged something? ─► PRAGMA wal_checkpoint(TRUNCATE) (WP-A3)

The stdout log does not depend on the database: when the disk is full, writing the system
event may fail, and monitoring must still see the problem. Housekeeping never stops the loop;
every failure is logged with its type only.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.backup import backup_directory, prune_backups
from app.adapters.db.maintenance import checkpoint_wal, measure_storage, reclaim_free_pages
from app.domain.booking_rollup import RollupResult
from app.domain.ports import Clock, UnitOfWork
from app.domain.retention_sweep import SweepResult
from app.domain.storage import (
    MIB,
    StorageGrade,
    StorageLimits,
    StorageUsage,
    StorageWatch,
    assess,
)
from app.domain.system_log import SystemLog
from app.domain.tunable import value_of

log = structlog.get_logger()

REMIND_EVERY = timedelta(hours=1)
"""How often a grade that is not ok is logged again."""

RECLAIM_EVERY = timedelta(hours=1)
"""How often freed database pages are returned to the file system."""

SWEEP_EVERY = timedelta(days=1)
"""How often the retention sweep runs (fixed privacy rule, CLAUDE.md: nightly)."""

ERASE_EVERY = timedelta(days=1)
"""How often archived groups are swept (ADR 0084 step 3); the delay itself is a setting."""


ROLLUP_EVERY = timedelta(days=1)
"""How often old booking rows are merged into daily counts; the term itself is a setting."""

SESSIONS_EVERY = timedelta(hours=1)
"""How often ended and expired admin sessions are deleted; the lifetime is a setting."""

STAFF_EVERY = timedelta(days=1)
"""How often staff records past their retention are erased; the period is a setting."""


BACKUPS_EVERY = timedelta(hours=1)
"""How often update back-ups past their lifetime are deleted (the lifetime is fixed, ADR 0102)."""


class Housekeeping:
    """Storage watch and page reclaim for the worker loop.

    Args:
        engine: The database engine.
        uow: Writes the system events.
        clock: Current time.
        limits: Storage grade boundaries.
        measure: Returns the current usage, or ``None`` when unknown (tests pass a fake).
        brake: Ends diagnostic mode, with the reason; called every loop at grade critical.
        erase: Erases the archived groups that qualify and returns the count; called once per
            ``ERASE_EVERY``.
        sweep: Runs the retention sweep and returns what it found; called once per
            ``SWEEP_EVERY``.
        clean_sessions: Deletes the ended and expired admin sessions and returns the count;
            called once per ``SESSIONS_EVERY``.
        erase_staff: Erases the staff records past ``staff_retention_days`` and returns the
            count; called once per ``STAFF_EVERY``.
        backups: The directory of the update back-ups (``data/backups``), or ``None`` to leave
            them to the updater; pruned once per ``BACKUPS_EVERY``.
        rollup: Merges old booking rows into daily counts and deletes them (ADR 0102 step 4);
            called once per ``ROLLUP_EVERY``.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        uow: UnitOfWork,
        clock: Clock,
        limits: StorageLimits | Callable[[], StorageLimits],
        measure: Callable[[], StorageUsage | None],
        brake: Callable[[str], Awaitable[int]] | None = None,
        erase: Callable[[], Awaitable[int]] | None = None,
        sweep: Callable[[], Awaitable[SweepResult]] | None = None,
        clean_sessions: Callable[[], Awaitable[int]] | None = None,
        erase_staff: Callable[[], Awaitable[int]] | None = None,
        backups: Path | None = None,
        rollup: Callable[[], Awaitable[RollupResult]] | None = None,
    ) -> None:
        """Store the collaborators; nothing has been logged, reclaimed or erased yet."""
        self._rollup = rollup
        self._rolled_up_at: datetime | None = None
        self._erase_staff = erase_staff
        self._staff_erased_at: datetime | None = None
        self._clean_sessions = clean_sessions
        self._backups = backups
        self._backups_pruned_at: datetime | None = None
        self._sessions_cleaned_at: datetime | None = None
        self._sweep = sweep
        self._swept_at: datetime | None = None
        self._brake = brake
        self._erase = erase
        self._erased_at: datetime | None = None
        self._engine = engine
        self._clock = clock
        self._limits = limits
        self._measure = measure
        self._watch = StorageWatch(SystemLog(uow, clock), limits)
        self._logged: tuple[str, datetime] | None = None
        self._reclaimed_at: datetime | None = None

    @classmethod
    def for_database(
        cls,
        engine: AsyncEngine,
        uow: UnitOfWork,
        clock: Clock,
        limits: StorageLimits | Callable[[], StorageLimits],
        database_url: str,
        brake: Callable[[str], Awaitable[int]] | None = None,
        erase: Callable[[], Awaitable[int]] | None = None,
        sweep: Callable[[], Awaitable[SweepResult]] | None = None,
        clean_sessions: Callable[[], Awaitable[int]] | None = None,
        erase_staff: Callable[[], Awaitable[int]] | None = None,
        rollup: Callable[[], Awaitable[RollupResult]] | None = None,
    ) -> "Housekeeping":
        """Return housekeeping that measures the SQLite file of ``database_url``."""
        return cls(
            engine,
            uow,
            clock,
            limits,
            lambda: measure_storage(database_url),
            brake,
            erase,
            sweep,
            clean_sessions,
            erase_staff,
            _backup_directory(database_url),
            rollup,
        )

    async def run(self) -> None:
        """Watch the storage, then reclaim pages and erase archived groups when due."""
        now = self._clock.now()
        await self._watch_storage(now)
        await self._reclaim(now)
        await self._erase_groups(now)
        await self._sweep_retention(now)
        await self._clean_admin_sessions(now)
        await self._erase_staff_records(now)
        self._prune_backups(now)
        await self._roll_up_bookings(now)

    def _prune_backups(self, now: datetime) -> None:
        """Delete update back-ups past their lifetime, at most once per ``BACKUPS_EVERY``."""
        if self._backups is None:
            return
        if self._backups_pruned_at is not None and now - self._backups_pruned_at < BACKUPS_EVERY:
            return
        self._backups_pruned_at = now
        try:
            deleted = prune_backups(self._backups, now)
        except Exception as exc:  # never stop the loop over housekeeping
            log.warning("backups.prune_failed", error=type(exc).__name__)
        else:
            if deleted:
                log.info("backups.pruned", backups=deleted)

    async def _erase_staff_records(self, now: datetime) -> None:
        """Erase staff records past their retention, at most once per ``STAFF_EVERY``."""
        if self._erase_staff is None:
            return
        if self._staff_erased_at is not None and now - self._staff_erased_at < STAFF_EVERY:
            return
        self._staff_erased_at = now
        try:
            erased = await self._erase_staff()
        except Exception as exc:  # never stop the loop over housekeeping
            log.warning("staff.erase_failed", error=type(exc).__name__)
        else:
            if erased:
                log.info("staff.erased", staff=erased)

    async def _roll_up_bookings(self, now: datetime) -> None:
        """Merge old purged booking rows into daily counts, at most once per ``ROLLUP_EVERY``."""
        if self._rollup is None:
            return
        if self._rolled_up_at is not None and now - self._rolled_up_at < ROLLUP_EVERY:
            return
        self._rolled_up_at = now
        try:
            result = await self._rollup()
        except Exception as exc:  # never stop the loop over housekeeping
            log.error("booking_rows.rollup_failed", error=type(exc).__name__)
        else:
            if result.rows or result.skipped:
                log.info(
                    "booking_rows.rollup",
                    rows=result.rows,
                    days=result.day_counts,
                    skipped=result.skipped,
                )

    async def _clean_admin_sessions(self, now: datetime) -> None:
        """Delete ended and expired admin sessions, at most once per ``SESSIONS_EVERY``."""
        if self._clean_sessions is None:
            return
        if (
            self._sessions_cleaned_at is not None
            and now - self._sessions_cleaned_at < SESSIONS_EVERY
        ):
            return
        self._sessions_cleaned_at = now
        try:
            deleted = await self._clean_sessions()
        except Exception as exc:  # never stop the loop over housekeeping
            log.warning("sessions.clean_failed", error=type(exc).__name__)
        else:
            if deleted:
                log.info("sessions.cleaned", sessions=deleted)

    async def _sweep_retention(self, now: datetime) -> None:
        """Purge overdue personal data, at most once per ``SWEEP_EVERY``; alert on a backlog."""
        if self._sweep is None:
            return
        if self._swept_at is not None and now - self._swept_at < SWEEP_EVERY:
            return
        self._swept_at = now
        try:
            result = await self._sweep()
        except Exception as exc:  # never stop the loop over housekeeping
            log.error("retention.sweep_failed", error=type(exc).__name__)
            return
        if result.purged:
            await self._checkpoint()
        if result.notices:
            log.warning("retention.closure_notice", closures=result.notices)
        if result.overdue:
            log.warning(
                "retention.sweep",
                overdue=result.overdue,
                purged=result.purged,
                failed=result.failed,
            )
        if result.backlog or result.failed:
            log.error("retention.backlog", bookings=result.backlog, failed=result.failed)

    async def _checkpoint(self) -> None:
        """Empty the WAL after a sweep that purged, so no older copy of the data stays (WP-A3)."""
        try:
            await checkpoint_wal(self._engine)
        except Exception as exc:  # the purge itself succeeded; the next checkpoint finishes it
            log.warning("retention.checkpoint_failed", error=type(exc).__name__)

    async def _erase_groups(self, now: datetime) -> None:
        """Erase the archived groups that qualify, at most once per ``ERASE_EVERY``."""
        if self._erase is None:
            return
        if self._erased_at is not None and now - self._erased_at < ERASE_EVERY:
            return
        self._erased_at = now
        try:
            erased = await self._erase()
        except Exception as exc:  # never stop the loop over housekeeping
            log.warning("group.erase_failed", error=type(exc).__name__)
        else:
            if erased:
                log.info("group.erase_done", groups=erased)

    async def _watch_storage(self, now: datetime) -> None:
        """Log a degraded grade and record a change; failures are only logged."""
        usage = self._measure()
        if usage is None:
            return
        grade, reason = assess(usage, value_of(self._limits))
        self._log(now, usage, grade, reason)
        if grade is StorageGrade.CRITICAL and self._brake is not None:
            try:
                ended = await self._brake(reason)
            except Exception as exc:  # the disk may be too full even for this
                log.warning("diagnostics.brake_failed", error=type(exc).__name__)
            else:
                if ended:
                    log.warning("diagnostics.braked", modes=ended)
        try:
            await self._watch.observe(usage)
        except Exception as exc:  # e.g. the disk is too full to write the event
            log.warning("storage.record_failed", error=type(exc).__name__)

    def _log(self, now: datetime, usage: StorageUsage, grade: StorageGrade, reason: str) -> None:
        """Log a grade that is not ok on change and then hourly; log the recovery once."""
        if grade is StorageGrade.OK:
            if self._logged is not None:
                log.info("storage.recovered")
                self._logged = None
            return
        if self._logged is not None:
            logged_reason, logged_at = self._logged
            if logged_reason == reason and now - logged_at < REMIND_EVERY:
                return
        level = logging.ERROR if grade is StorageGrade.CRITICAL else logging.WARNING
        log.log(
            level,
            "storage.degraded",
            grade=grade.value,
            reason=reason,
            free_percent=int(usage.free_percent),
            database_mb=usage.database_bytes // MIB,
        )
        self._logged = (reason, now)

    async def _reclaim(self, now: datetime) -> None:
        """Return freed pages to the file system, at most once per ``RECLAIM_EVERY``."""
        if self._reclaimed_at is not None and now - self._reclaimed_at < RECLAIM_EVERY:
            return
        self._reclaimed_at = now
        try:
            pages = await reclaim_free_pages(self._engine)
        except Exception as exc:  # housekeeping; jobs come first
            log.warning("database.reclaim_failed", error=type(exc).__name__)
            return
        if pages:
            log.info("database.reclaimed", pages=pages)


def _backup_directory(database_url: str) -> Path | None:
    """Return the back-up directory next to the SQLite file, or ``None`` for another database."""
    try:
        return backup_directory(database_url)
    except ValueError:
        return None
