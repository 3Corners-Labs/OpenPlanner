"""Storage limits: grade free disk space and database size, and report changes (ADR 0040).

The worker measures the data volume every loop; this module decides what the numbers mean.
A change of grade becomes one global system event, so only the superuser sees it (ADR 0031);
a steady grade records nothing, so a full disk cannot flood the audit log.

    measure ─► assess(usage, limits) ─► ok | low | critical (+ reason)
            ─► grade changed? ─► system event storage.check (failure, or success on recovery)
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.audit import Outcome
from app.domain.system_log import SystemLog

MIB = 1024 * 1024
"""Bytes per mebibyte, the unit of ``DATABASE_MAX_MB`` and of the event detail."""

STORAGE_EVENT = "storage.check"
"""System event key of a change of storage grade."""

FREE_SPACE_LOW = "free_space_low"
FREE_SPACE_CRITICAL = "free_space_critical"
DATABASE_TOO_LARGE = "database_too_large"


class StorageGrade(StrEnum):
    """How close the data volume is to full."""

    OK = "ok"
    LOW = "low"
    CRITICAL = "critical"


@dataclass(frozen=True)
class StorageUsage:
    """One measurement of the data volume.

    Attributes:
        free_bytes: Space still available to the application.
        total_bytes: Size of the file system.
        database_bytes: Size of the database with its write-ahead log.
    """

    free_bytes: int
    total_bytes: int
    database_bytes: int

    @property
    def free_percent(self) -> float:
        """Free space as a percentage of the file system; an empty file system counts as full."""
        return 100 * self.free_bytes / self.total_bytes if self.total_bytes else 0.0


@dataclass(frozen=True)
class StorageLimits:
    """Where the grades start.

    Attributes:
        low_free_percent: Below this much free space the grade is low.
        critical_free_percent: Below this much free space the grade is critical.
        database_max_bytes: Above this database size the grade is low; ``None`` for no limit.

    Raises:
        ValueError: If the critical limit is not below the low one.
    """

    low_free_percent: float = 15.0
    critical_free_percent: float = 5.0
    database_max_bytes: int | None = None

    def __post_init__(self) -> None:
        """Check that the limits are ordered."""
        if not self.critical_free_percent < self.low_free_percent:
            raise ValueError("critical free percent must be below the low free percent")


def assess(usage: StorageUsage, limits: StorageLimits) -> tuple[StorageGrade, str]:
    """Return the grade of ``usage`` and its reason code (empty when the grade is ok)."""
    free = usage.free_percent
    if free < limits.critical_free_percent:
        return StorageGrade.CRITICAL, FREE_SPACE_CRITICAL
    if free < limits.low_free_percent:
        return StorageGrade.LOW, FREE_SPACE_LOW
    if limits.database_max_bytes is not None and usage.database_bytes > limits.database_max_bytes:
        return StorageGrade.LOW, DATABASE_TOO_LARGE
    return StorageGrade.OK, ""


@dataclass(frozen=True)
class StorageCheck:
    """The outcome of one observation.

    Attributes:
        grade: The current grade.
        reason: Its reason code; empty when ok.
        changed: Whether the grade differs from the previous observation.
    """

    grade: StorageGrade
    reason: str
    changed: bool


class StorageWatch:
    """Remembers the last grade and records a system event when it changes.

    The watch starts at ok, so a healthy start records nothing and an unhealthy start is
    reported at once.

    Args:
        system_log: Where the events go.
        limits: The grade boundaries.
    """

    def __init__(self, system_log: SystemLog, limits: StorageLimits) -> None:
        """Start at grade ok."""
        self._system_log = system_log
        self._limits = limits
        self._reason = ""

    async def observe(self, usage: StorageUsage) -> StorageCheck:
        """Grade ``usage``; record a system event when the grade changed.

        The new grade is remembered only after the event is written, so a failed write is
        tried again on the next observation.

        Raises:
            Exception: Whatever writing the event raises.
        """
        grade, reason = assess(usage, self._limits)
        changed = reason != self._reason
        if changed:
            await self._system_log.record(
                STORAGE_EVENT,
                Outcome.SUCCESS if grade is StorageGrade.OK else Outcome.FAILURE,
                reason=reason,
                detail=(
                    f"free_percent:{int(usage.free_percent)},"
                    f"database_mb:{usage.database_bytes // MIB}"
                ),
            )
            self._reason = reason
        return StorageCheck(grade, reason, changed)
