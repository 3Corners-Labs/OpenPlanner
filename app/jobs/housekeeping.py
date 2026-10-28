"""Worker housekeeping: watch the data volume and give freed database pages back (ADR 0040).

Called once per worker loop, after the due jobs:

    measure ─► grade ─► not ok? ─► log storage.degraded (on change, then hourly)
                     ─► changed? ─► system event storage.check (superuser's audit log)
    hourly ─► PRAGMA incremental_vacuum ─► pages back to the file system

The stdout log does not depend on the database: when the disk is full, writing the system
event may fail, and monitoring must still see the problem. Housekeeping never stops the loop;
every failure is logged with its type only.
"""

from collections.abc import Callable
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

log = structlog.get_logger()

REMIND_EVERY = timedelta(hours=1)
"""How often a grade that is not ok is logged again."""

RECLAIM_EVERY = timedelta(hours=1)
"""How often freed database pages are returned to the file system."""


class Housekeeping:
    """Storage watch and page reclaim for the worker loop.

    Args:
        engine: The database engine.
        uow: Writes the system events.
        clock: Current time.
        limits: Storage grade boundaries.
        measure: Returns the current usage, or ``None`` when unknown (tests pass a fake).
    """

    def __init__(
        self,
        engine: AsyncEngine,
        uow: UnitOfWork,
        clock: Clock,
        limits: StorageLimits,
        measure: Callable[[], StorageUsage | None],
    ) -> None:
        """Store the collaborators; nothing has been logged or reclaimed yet."""
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
        limits: StorageLimits,
        database_url: str,
    ) -> "Housekeeping":
        """Return housekeeping that measures the SQLite file of ``database_url``."""
        return cls(engine, uow, clock, limits, lambda: measure_storage(database_url))

    async def run(self) -> None:
        """Watch the storage, then reclaim pages when due."""
        now = self._clock.now()
        await self._watch_storage(now)
        await self._reclaim(now)

    async def _watch_storage(self, now: datetime) -> None:
        """Log a degraded grade and record a change; failures are only logged."""
        usage = self._measure()
        if usage is None:
            return
        grade, reason = assess(usage, self._limits)
        self._log(now, usage, grade, reason)
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
