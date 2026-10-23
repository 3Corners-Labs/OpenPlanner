from datetime import UTC, datetime, timedelta


class FixedClock:
    """Clock fake: returns a fixed instant until advanced explicitly."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 1, 5, 9, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
