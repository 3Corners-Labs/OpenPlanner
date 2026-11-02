"""How old the update back-ups may be, and when System status warns (ADR 0102 points 7 and 10).

The updater's back-up is a rollback copy: it lives ``BACKUP_LIFETIME`` and is then deleted by
the updater and, as a second safety net, by the worker. Both are fixed on purpose: the lifetime
is the restore window of an update, not a retention policy, and switching it off is never a
setting (ADR 0042). A copy that is still there after the lifetime plus ``BACKUP_GRACE`` means
neither deleter worked, so System status warns.

    backup_grade(oldest, now)  ok, expired (to delete) or overdue (warn)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

BACKUP_LIFETIME = timedelta(hours=24)
"""How long a backup is kept: the restore window of an update (``RESTORE_WINDOW``)."""

BACKUP_GRACE = timedelta(hours=2)
"""How long past ``BACKUP_LIFETIME`` a copy may stay before System status warns: the updater
and the worker prune on a timer, so a copy is a little older than the lifetime before it goes."""


class BackupGrade(StrEnum):
    """How an update back-up stands against its lifetime."""

    NONE = "none"
    OK = "ok"
    OVERDUE = "overdue"


@dataclass(frozen=True)
class BackupAge:
    """The oldest update back-up on the data volume.

    Attributes:
        count: How many back-ups there are.
        oldest_since: When the oldest was written (UTC).
    """

    count: int
    oldest_since: datetime


def backup_grade(oldest: BackupAge | None, now: datetime) -> BackupGrade:
    """Return ``NONE`` without a back-up, ``OVERDUE`` past lifetime plus grace, else ``OK``."""
    if oldest is None:
        return BackupGrade.NONE
    if now - oldest.oldest_since > BACKUP_LIFETIME + BACKUP_GRACE:
        return BackupGrade.OVERDUE
    return BackupGrade.OK
