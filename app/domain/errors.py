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
    OUTSIDE_BOOKING_WINDOW = "outside_booking_window"
    BOOKING_NOT_FOUND = "booking_not_found"
    BOOKING_NOT_ACTIVE = "booking_not_active"
    BOOKING_LOCKED = "booking_locked"
    SAME_SLOT = "same_slot"
    DATABASE_BUSY = "database_busy"
    NOT_PERMITTED = "not_permitted"
    ORGANISATION_NOT_FOUND = "organisation_not_found"
    SLUG_TAKEN = "slug_taken"
    GROUP_HAS_FUTURE_BOOKINGS = "group_has_future_bookings"
    SCHEDULE_ITEM_NOT_FOUND = "schedule_item_not_found"
    INTEGRATION_NOT_READY = "integration_not_ready"


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
