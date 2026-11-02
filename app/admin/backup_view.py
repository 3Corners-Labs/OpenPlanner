"""The *Update back-ups* part of System status (ADR 0102 point 10).

Shows how many rollback copies of the database there are and how old the oldest is. The updater
and the worker delete a copy after 24 hours; a copy that is still there well past that means
neither did, and it holds personal data. Nothing here can be changed. Only counts and times are
shown, never a file name.

    backup_view(...)  the whole part, for the System status tab
"""

from dataclasses import dataclass

from fastapi import Request

from app.adapters.db.backup import backup_directory, oldest_backup
from app.admin.pages import admin_moment
from app.config import Settings
from app.domain.backup_age import BACKUP_GRACE, BACKUP_LIFETIME, BackupGrade, backup_grade
from app.web.locale import RequestLocale

_SECONDS_PER_HOUR = 3600


@dataclass(frozen=True)
class BackupView:
    """The *Update back-ups* part as the template shows it."""

    count: int
    oldest: str
    warning: str


def backup_view(request: Request, locale: RequestLocale, settings: Settings) -> BackupView:
    """Describe the back-ups on the data volume; an unreadable directory counts as none."""
    _ = locale.gettext
    now = request.app.state.clock.now()
    try:
        found = oldest_backup(backup_directory(settings.database_url))
    except (OSError, ValueError):
        found = None
    if found is None:
        return BackupView(0, "", "")
    hours = int((now - found.oldest_since).total_seconds() // _SECONDS_PER_HOUR)
    oldest = _(
        "admin.settings.backups.oldest.value",
        hours=max(hours, 0),
        since=admin_moment(request, locale, found.oldest_since),
    )
    warning = ""
    if backup_grade(found, now) is BackupGrade.OVERDUE:
        limit = int((BACKUP_LIFETIME + BACKUP_GRACE).total_seconds() // _SECONDS_PER_HOUR)
        warning = _("admin.settings.backups.overdue", hours=limit)
    return BackupView(found.count, oldest, warning)
