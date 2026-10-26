"""Catalogue keys for update actions, statuses and the host updater's reasons (ADR 0029).

Written out literally so the catalogue test can check that every one exists in Dutch and
English. A reason the portal does not know (a newer updater) is shown as "other".
"""

from app.domain.updates import UpdateAction, UpdateStatus

ACTION_LABELS: dict[UpdateAction, str] = {
    UpdateAction.UPDATE: "admin.updates.action.update",
    UpdateAction.RESTORE: "admin.updates.action.restore",
}

STATUS_LABELS: dict[UpdateStatus, str] = {
    UpdateStatus.REQUESTED: "admin.updates.status.requested",
    UpdateStatus.RUNNING: "admin.updates.status.running",
    UpdateStatus.SUCCEEDED: "admin.updates.status.succeeded",
    UpdateStatus.ROLLED_BACK: "admin.updates.status.rolled_back",
    UpdateStatus.FAILED: "admin.updates.status.failed",
    UpdateStatus.CANCELLED: "admin.updates.status.cancelled",
}

REASON_LABELS: dict[str, str] = {
    "signature_invalid": "admin.updates.reason.signature_invalid",
    "image_not_found": "admin.updates.reason.image_not_found",
    "backup_failed": "admin.updates.reason.backup_failed",
    "migration_failed": "admin.updates.reason.migration_failed",
    "health_check_failed": "admin.updates.reason.health_check_failed",
    "restore_failed": "admin.updates.reason.restore_failed",
    "version_mismatch": "admin.updates.reason.version_mismatch",
    "updater_error": "admin.updates.reason.updater_error",
}

OTHER_REASON = "admin.updates.reason.other"


def reason_label(reason: str) -> str:
    """Return the catalogue key of an updater's reason code."""
    return REASON_LABELS.get(reason, OTHER_REASON)
