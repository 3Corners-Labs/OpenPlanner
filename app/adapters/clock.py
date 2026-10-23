"""System clock adapter for the ``Clock`` port."""

from datetime import UTC, datetime


class SystemClock:
    """``Clock`` backed by the operating system time."""

    def now(self) -> datetime:
        """Return the current time as an aware UTC datetime."""
        return datetime.now(UTC)
