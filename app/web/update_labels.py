"""Catalogue keys for update actions, statuses, steps and the updater's reasons (ADR 0029, 0048).

Written out literally so the catalogue test can check that every one exists in Dutch and
English. A reason the portal does not know (a newer updater) is shown as "other".
"""

from app.domain.updates import UpdateAction, UpdateStatus, UpdateStep

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

STEP_LABELS: dict[UpdateStep, str] = {
    UpdateStep.WAITING: "admin.updates.step.waiting",
    UpdateStep.PULLING: "admin.updates.step.pulling",
    UpdateStep.VERIFYING: "admin.updates.step.verifying",
    UpdateStep.BACKING_UP: "admin.updates.step.backing_up",
    UpdateStep.MIGRATING: "admin.updates.step.migrating",
    UpdateStep.STARTING: "admin.updates.step.starting",
    UpdateStep.ROLLING_BACK: "admin.updates.step.rolling_back",
}
"""Step labels; they may use ``{version}``, the request's target."""

STEP_STATE_LABELS: dict[str, str] = {
    "done": "admin.updates.step_state.done",
    "current": "admin.updates.step_state.current",
    "pending": "admin.updates.step_state.pending",
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


HISTORY_LABELS: dict[str, str] = {
    "update.request": "admin.updates.history.update_request",
    "update.auto_request": "admin.updates.history.update_auto_request",
    "update.succeeded": "admin.updates.history.update_succeeded",
    "update.failed": "admin.updates.history.update_failed",
    "update.rolled_back": "admin.updates.history.update_rolled_back",
    "restore.request": "admin.updates.history.restore_request",
    "restore.succeeded": "admin.updates.history.restore_succeeded",
    "restore.failed": "admin.updates.history.restore_failed",
    "restore.rolled_back": "admin.updates.history.restore_rolled_back",
    "request.cancel": "admin.updates.history.request_cancel",
    "version.changed": "admin.updates.history.version_changed",
}
"""What a history line says, by the first key of the audit detail (ADR 0065)."""

ACTOR_LABELS: dict[str, str] = {
    "system": "admin.updates.actor.system",
    "host-updater": "admin.updates.actor.updater",
    "host": "admin.updates.actor.host",
}
"""Who did it, for the actors that are not a person."""
