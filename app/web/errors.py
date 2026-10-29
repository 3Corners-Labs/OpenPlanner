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
    ErrorCode.API_CLIENT_NOT_FOUND: "error.not_found.text",
    ErrorCode.CHANNEL_NOT_CONFIGURED: "error.invalid_request",
    ErrorCode.REMINDER_EXISTS: "admin.form.error.reminder_exists",
    ErrorCode.REMINDER_NOT_FOUND: "error.not_found.text",
    ErrorCode.UPDATE_NOT_AVAILABLE: "admin.form.error.update_not_available",
    ErrorCode.UPDATE_IN_PROGRESS: "admin.form.error.update_in_progress",
    ErrorCode.UPDATE_REQUEST_NOT_FOUND: "error.not_found.text",
    ErrorCode.PLANNER_TOO_LATE: "admin.form.error.planner_too_late",
    ErrorCode.MOVE_CAUSES_SHORTFALL: "admin.form.error.move_causes_shortfall",
}

FIELD_ERROR_KEYS: dict[str, str] = {
    "name": "error.field.name",
    "email": "error.field.email",
    "phone": "error.field.phone",
}

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_CONTACT: 422,
    ErrorCode.INVALID_RANGE: 400,
    ErrorCode.GROUP_NOT_FOUND: 404,
    ErrorCode.BOOKING_NOT_FOUND: 404,
    ErrorCode.SCHEDULE_ITEM_NOT_FOUND: 404,
    ErrorCode.REMINDER_NOT_FOUND: 404,
    ErrorCode.ORGANISATION_NOT_FOUND: 404,
    ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND: 404,
    ErrorCode.HOLIDAY_NOT_FOUND: 404,
    ErrorCode.CLOSURE_NOT_FOUND: 404,
    ErrorCode.API_CLIENT_NOT_FOUND: 404,
    ErrorCode.UPDATE_REQUEST_NOT_FOUND: 404,
    ErrorCode.NOT_PERMITTED: 403,
    ErrorCode.DATABASE_BUSY: 503,
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
