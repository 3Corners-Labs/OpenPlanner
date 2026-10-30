"""Turn domain error codes into HTTP statuses and translated messages.

The domain only knows codes (``DomainError``); this module is the one place that maps them
to text. Messages never contain submitted values, so no personal data is echoed back.
"""

from dataclasses import dataclass

from app.domain.errors import DomainError, ErrorCode
from app.i18n import BoundGettext

ERROR_KEYS: dict[ErrorCode, str] = {
    ErrorCode.SLOT_FULL: "error.slot_full",
    ErrorCode.SLOT_UNAVAILABLE: "error.slot_unavailable",
    ErrorCode.OUTSIDE_BOOKING_WINDOW: "error.outside_booking_window",
    ErrorCode.BOOKING_LOCKED: "error.booking_locked",
    ErrorCode.BOOKING_NOT_ACTIVE: "error.booking_not_active",
    ErrorCode.BOOKING_NOT_PENDING: "error.booking_not_pending",
    ErrorCode.SAME_SLOT: "error.same_slot",
    ErrorCode.DATABASE_BUSY: "error.database_busy",
    ErrorCode.INVALID_CONTACT: "error.invalid_request",
    ErrorCode.INVALID_RANGE: "error.invalid_request",
    ErrorCode.INVALID_CONFIGURATION: "error.invalid_request",
    ErrorCode.GROUP_NOT_FOUND: "error.not_found.text",
    ErrorCode.BOOKING_NOT_FOUND: "error.not_found.text",
    ErrorCode.SCHEDULE_ITEM_NOT_FOUND: "error.not_found.text",
    ErrorCode.ORGANISATION_NOT_FOUND: "error.not_found.text",
    ErrorCode.NOT_PERMITTED: "admin.forbidden.text",
    ErrorCode.SLUG_TAKEN: "admin.form.error.slug_taken",
    ErrorCode.GROUP_HAS_FUTURE_BOOKINGS: "admin.form.error.future_bookings",
    ErrorCode.INTEGRATION_NOT_READY: "error.invalid_request",
    ErrorCode.DIRECT_CALL_UNAVAILABLE: "error.direct_call_unavailable",
    ErrorCode.DIRECT_CALL_FULL: "error.direct_call_full",
    ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND: "error.not_found.text",
    ErrorCode.HOLIDAY_CALENDAR_IN_USE: "admin.form.error.calendar_in_use",
    ErrorCode.HOLIDAY_NOT_FOUND: "error.not_found.text",
    ErrorCode.GROUP_CLOSED: "error.group_closed",
    ErrorCode.CLOSURE_EXISTS: "admin.form.error.closure_exists",
    ErrorCode.CLOSURE_NOT_FOUND: "error.not_found.text",
    ErrorCode.SCENARIO_NOT_FOUND: "error.not_found.text",
    ErrorCode.API_CLIENT_NOT_FOUND: "error.not_found.text",
    ErrorCode.CHANNEL_NOT_CONFIGURED: "error.invalid_request",
    ErrorCode.REMINDER_EXISTS: "admin.form.error.reminder_exists",
    ErrorCode.REMINDER_NOT_FOUND: "error.not_found.text",
    ErrorCode.UPDATE_NOT_AVAILABLE: "admin.form.error.update_not_available",
    ErrorCode.UPDATE_IN_PROGRESS: "admin.form.error.update_in_progress",
    ErrorCode.UPDATE_REQUEST_NOT_FOUND: "error.not_found.text",
    ErrorCode.PLANNER_TOO_LATE: "admin.form.error.planner_too_late",
    ErrorCode.MOVE_CAUSES_SHORTFALL: "admin.form.error.move_causes_shortfall",
    ErrorCode.SLOT_CHANGED: "admin.form.error.slot_changed",
    ErrorCode.ROLE_NOT_FOUND: "error.not_found.text",
    ErrorCode.ROLE_INVALID: "admin.form.error.role_invalid",
    ErrorCode.ROLE_CHANGED: "admin.form.error.role_changed",
    ErrorCode.CLAIM_VALUE_TAKEN: "admin.form.error.claim_value_taken",
    ErrorCode.MAINTENANCE: "error.maintenance",
    ErrorCode.MAINTENANCE_FROM_ENVIRONMENT: "admin.form.error.maintenance_from_environment",
    ErrorCode.QUEUE_NOT_ENABLED: "error.not_found.text",
    ErrorCode.QUEUE_EMPTY: "error.queue_empty",
    ErrorCode.ALREADY_CLAIMED: "error.already_claimed",
    ErrorCode.FREE_PICK_NOT_ALLOWED: "error.free_pick_not_allowed",
    ErrorCode.NOT_CLAIMED: "error.not_claimed",
    ErrorCode.SETTING_NOT_EDITABLE: "admin.form.error.setting_not_editable",
    ErrorCode.SETTING_LOCKED: "admin.form.error.setting_locked",
    ErrorCode.SETTING_INVALID: "admin.form.error.setting_invalid",
    ErrorCode.SETTING_COMBINATION: "admin.form.error.setting_combination",
    ErrorCode.SETTING_EXPIRY: "admin.form.error.setting_expiry",
    ErrorCode.INVALID_FORM_FIELD: "admin.form.error.invalid_form_field",
    ErrorCode.FORM_FIELD_FORBIDDEN: "admin.form.error.form_field_forbidden",
    ErrorCode.INVALID_ANSWER: "error.invalid_answer",
    ErrorCode.SETTING_NO_KEY: "admin.form.error.setting_no_key",
    ErrorCode.SETTING_NOT_LOCKED: "admin.form.error.setting_not_locked",
}

FIELD_ERROR_KEYS: dict[str, str] = {
    "first_name": "error.field.first_name",
    "last_name": "error.field.last_name",
    "email": "error.field.email",
    "phone": "error.field.phone",
}

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_CONTACT: 422,
    ErrorCode.INVALID_ANSWER: 422,
    ErrorCode.INVALID_FORM_FIELD: 422,
    ErrorCode.FORM_FIELD_FORBIDDEN: 422,
    ErrorCode.INVALID_RANGE: 400,
    ErrorCode.GROUP_NOT_FOUND: 404,
    ErrorCode.BOOKING_NOT_FOUND: 404,
    ErrorCode.SCHEDULE_ITEM_NOT_FOUND: 404,
    ErrorCode.REMINDER_NOT_FOUND: 404,
    ErrorCode.ORGANISATION_NOT_FOUND: 404,
    ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND: 404,
    ErrorCode.HOLIDAY_NOT_FOUND: 404,
    ErrorCode.CLOSURE_NOT_FOUND: 404,
    ErrorCode.SCENARIO_NOT_FOUND: 404,
    ErrorCode.API_CLIENT_NOT_FOUND: 404,
    ErrorCode.UPDATE_REQUEST_NOT_FOUND: 404,
    ErrorCode.ROLE_NOT_FOUND: 404,
    ErrorCode.QUEUE_NOT_ENABLED: 404,
    ErrorCode.NOT_PERMITTED: 403,
    ErrorCode.DATABASE_BUSY: 503,
    ErrorCode.MAINTENANCE: 503,
    ErrorCode.INVALID_CONFIGURATION: 500,
}
"""Codes not listed here are conflicts with the current state: HTTP 409."""


@dataclass(frozen=True)
class FormError:
    """One message for the error summary; ``field`` links it to an input (or ``None``)."""

    field: str | None
    message: str


def status_for(error: DomainError) -> int:
    """Return the HTTP status for a domain error."""
    return HTTP_STATUS.get(error.code, 409)


def form_errors(error: DomainError, _: BoundGettext) -> list[FormError]:
    """Return the translated messages for ``error``, linked to a field when it names one."""
    if error.field in FIELD_ERROR_KEYS:
        return [FormError(error.field, _(FIELD_ERROR_KEYS[error.field]))]
    return [FormError(None, _(ERROR_KEYS[error.code]))]
