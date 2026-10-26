"""Slot values in forms: an appointment start as submitted by a participant or an admin."""

from datetime import datetime

MAX_SLOT_VALUE_LENGTH = 32


def parse_start(value: str | None) -> datetime | None:
    """Parse a slot value; anything but an aware ISO timestamp is ``None``."""
    if not value or len(value) > MAX_SLOT_VALUE_LENGTH:
        return None
    try:
        start = datetime.fromisoformat(value)
    except ValueError:
        return None
    return start if start.tzinfo is not None else None
