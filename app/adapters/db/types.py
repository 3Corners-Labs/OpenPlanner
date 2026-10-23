"""Column types that keep the domain's invariants at the database boundary."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Store aware datetimes as UTC; always return aware UTC datetimes.

    SQLite has no time zone type and SQLAlchemy would silently drop the offset. This type
    refuses naive values on the way in and attaches UTC on the way out (CLAUDE.md: all time
    stored as UTC with tzinfo).
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Convert to naive UTC for storage; reject naive input."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime values must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        """Attach UTC to the stored value."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if isinstance(value, datetime) else value
