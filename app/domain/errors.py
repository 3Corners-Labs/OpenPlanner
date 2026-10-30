"""Domain errors: machine-readable codes, never user-facing text.

Entry points map an ``ErrorCode`` to an HTTP status and an i18n key (``error.<code>``).
``field`` names the offending input field for validation errors; it never carries the value,
so no personal data can leak through an error.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Every reason a domain operation can refuse."""

    INVALID_CONFIGURATION = "invalid_configuration"
    INVALID_RANGE = "invalid_range"
    INVALID_CONTACT = "invalid_contact"
    GROUP_NOT_FOUND = "group_not_found"
    SLOT_UNAVAILABLE = "slot_unavailable"
    SLOT_FULL = "slot_full"
    BOOKING_LIMIT_REACHED = "booking_limit_reached"
    OUTSIDE_BOOKING_WINDOW = "outside_booking_window"
    BOOKING_NOT_FOUND = "booking_not_found"
    BOOKING_NOT_ACTIVE = "booking_not_active"
    BOOKING_NOT_PENDING = "booking_not_pending"
    BOOKING_LOCKED = "booking_locked"
    SAME_SLOT = "same_slot"
    DATABASE_BUSY = "database_busy"
    NOT_PERMITTED = "not_permitted"
    ORGANISATION_NOT_FOUND = "organisation_not_found"
    SLUG_TAKEN = "slug_taken"
    GROUP_HAS_FUTURE_BOOKINGS = "group_has_future_bookings"
    SCHEDULE_ITEM_NOT_FOUND = "schedule_item_not_found"
    INTEGRATION_NOT_READY = "integration_not_ready"
    DIRECT_CALL_UNAVAILABLE = "direct_call_unavailable"
    DIRECT_CALL_FULL = "direct_call_full"
    HOLIDAY_CALENDAR_NOT_FOUND = "holiday_calendar_not_found"
    HOLIDAY_CALENDAR_IN_USE = "holiday_calendar_in_use"
    HOLIDAY_NOT_FOUND = "holiday_not_found"
    GROUP_CLOSED = "group_closed"
    CLOSURE_EXISTS = "closure_exists"
    CLOSURE_NOT_FOUND = "closure_not_found"
    SCENARIO_NOT_FOUND = "scenario_not_found"
    API_CLIENT_NOT_FOUND = "api_client_not_found"
    CHANNEL_NOT_CONFIGURED = "channel_not_configured"
    REMINDER_EXISTS = "reminder_exists"
    REMINDER_NOT_FOUND = "reminder_not_found"
    UPDATE_NOT_AVAILABLE = "update_not_available"
    UPDATE_IN_PROGRESS = "update_in_progress"
    UPDATE_REQUEST_NOT_FOUND = "update_request_not_found"
    PLANNER_TOO_LATE = "planner_too_late"
    MOVE_CAUSES_SHORTFALL = "move_causes_shortfall"
    SLOT_CHANGED = "slot_changed"
    ROLE_NOT_FOUND = "role_not_found"
    ROLE_INVALID = "role_invalid"
    ROLE_CHANGED = "role_changed"
    CLAIM_VALUE_TAKEN = "claim_value_taken"
    MAINTENANCE = "maintenance"
    MAINTENANCE_FROM_ENVIRONMENT = "maintenance_from_environment"
    QUEUE_NOT_ENABLED = "queue_not_enabled"
    QUEUE_EMPTY = "queue_empty"
    ALREADY_CLAIMED = "already_claimed"
    FREE_PICK_NOT_ALLOWED = "free_pick_not_allowed"
    NOT_CLAIMED = "not_claimed"
    SETTING_NOT_EDITABLE = "setting_not_editable"
    SETTING_LOCKED = "setting_locked"
    SETTING_INVALID = "setting_invalid"
    SETTING_COMBINATION = "setting_combination"
    SETTING_EXPIRY = "setting_expiry"
    INVALID_FORM_FIELD = "invalid_form_field"
    FORM_FIELD_FORBIDDEN = "form_field_forbidden"
    INVALID_ANSWER = "invalid_answer"
    SETTING_NO_KEY = "setting_no_key"
    SETTING_NOT_LOCKED = "setting_not_locked"
    LOGIN_LOCKOUT = "login_lockout"
    TLS_CERTIFICATE_UNREADABLE = "tls_certificate_unreadable"
    TLS_KEY_MISMATCH = "tls_key_mismatch"
    TLS_NAME_MISMATCH = "tls_name_mismatch"
    TLS_CERTIFICATE_EXPIRED = "tls_certificate_expired"
    TLS_CERTIFICATE_NOT_YET_VALID = "tls_certificate_not_yet_valid"
    TLS_CERTIFICATE_MISSING = "tls_certificate_missing"
    TLS_CERTIFICATE_IN_USE = "tls_certificate_in_use"
    TLS_SOURCE_NOT_APPLICABLE = "tls_source_not_applicable"


class DomainError(Exception):
    """A business rule refused the operation.

    Args:
        code: Why the operation was refused.
        field: For validation errors, the name of the offending field (never its value).
    """

    def __init__(self, code: ErrorCode, field: str | None = None) -> None:
        """Store the code and field; the message is the code itself."""
        super().__init__(code.value if field is None else f"{code.value}: {field}")
        self.code = code
        self.field = field
