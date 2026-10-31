"""Worker housekeeping: watch the data volume and give freed database pages back (ADR 0040).

Called once per worker loop, after the due jobs:

    measure ─► grade ─► not ok? ─► log storage.degraded (on change, then hourly)
                     ─► changed? ─► system event storage.check (superuser's audit log)
    critical ─► brake: every diagnostic mode ends, so no debug events are written (part 4)
    hourly ─► PRAGMA incremental_vacuum ─► pages back to the file system
    daily ─► erase the archived groups nothing refers to any more (ADR 0084 step 3)

The stdout log does not depend on the database: when the disk is full, writing the system
event may fail, and monitoring must still see the problem. Housekeeping never stops the loop;
every failure is logged with its type only.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.maintenance import measure_storage, reclaim_free_pages
from app.domain.ports import Clock, UnitOfWork
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

ERASE_EVERY = timedelta(days=1)
"""How often archived groups are swept (ADR 0084 step 3); the delay itself is a setting."""


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
    ) -> None:
        """Store the collaborators; nothing has been logged, reclaimed or erased yet."""
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
    ) -> "Housekeeping":
        """Return housekeeping that measures the SQLite file of ``database_url``."""
        return cls(engine, uow, clock, limits, lambda: measure_storage(database_url), brake, erase)

    async def run(self) -> None:
        """Watch the storage, then reclaim pages and erase archived groups when due."""
        now = self._clock.now()
        await self._watch_storage(now)
        await self._reclaim(now)
        await self._erase_groups(now)

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
        emit = log.error if grade is StorageGrade.CRITICAL else log.warning
        emit(
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
