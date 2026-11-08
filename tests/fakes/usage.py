"""In-memory ``UsageStore`` and ``UsageUnitOfWork``: the rollup's and the backfill's fake.

The fake keeps facts, hourly cells, the watermark and the backfill state in lists, and applies
the same rules as the SQL adapter: a cell adds to the cell of its hour and cut, the watermark
only moves forwards, and a fact the rollup has not read is never pruned.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.usage import StaffFact, UsageCell, UsageFact
from app.domain.usage_backfill import BackfillBooking
from app.domain.usage_rollup import UsageBatch, UsagePruned, UsageState, merge


@dataclass
class UsageData:
    """What the fake store holds."""

    events: list[tuple[int, UsageFact | StaffFact]] = field(default_factory=list)
    cells: list[UsageCell] = field(default_factory=list)
    last_event_id: int = 0
    rolled_at: datetime | None = None
    backfilled_at: datetime | None = None
    cutoff: datetime | None = None
    bookings: list[BackfillBooking] = field(default_factory=list)
    done: set[int] = field(default_factory=set)
    next_id: int = 1
    enabled: bool = True


class InMemoryUsageStore:
    """The ``UsageStore`` over ``UsageData``."""

    def __init__(self, data: UsageData) -> None:
        self.data = data

    async def record_usage(self, fact: UsageFact | StaffFact) -> bool:
        if not self.data.enabled:
            return False
        self.data.events.append((self.data.next_id, fact))
        self.data.next_id += 1
        return True

    async def read_usage_events(self, after_id: int, limit: int) -> UsageBatch:
        rows = [(i, f) for i, f in self.data.events if i > after_id][:limit]
        return UsageBatch(tuple(f for _, f in rows), rows[-1][0] if rows else after_id)

    async def usage_state(self) -> UsageState:
        pending = sum(1 for i, _ in self.data.events if i > self.data.last_event_id)
        return UsageState(
            self.data.last_event_id, self.data.rolled_at, pending, self.data.backfilled_at
        )

    async def add_usage_cells(self, cells: Sequence[UsageCell]) -> None:
        self.data.cells = merge([*self.data.cells, *cells])

    async def advance_usage_watermark(self, last_event_id: int, at: datetime) -> None:
        self.data.last_event_id = max(self.data.last_event_id, last_event_id)
        self.data.rolled_at = at

    async def find_unbackfilled(self, after_id: int, limit: int) -> list[BackfillBooking]:
        found = [b for b in self.data.bookings if b.id > after_id and b.id not in self.data.done]
        return sorted(found, key=lambda b: b.id)[:limit]

    async def mark_backfilled(self, booking_ids: Sequence[int]) -> None:
        self.data.done.update(booking_ids)

    async def complete_backfill(self, at: datetime) -> None:
        self.data.backfilled_at = at

    async def usage_cutoff(self, now: datetime) -> datetime:
        if self.data.cutoff is None:
            self.data.cutoff = now.replace(minute=0, second=0, microsecond=0)
        return self.data.cutoff

    async def prune_usage(
        self, events_before: datetime, hours_before: datetime, limit: int
    ) -> UsagePruned:
        old = [
            i
            for i, f in self.data.events
            if i <= self.data.last_event_id and f.bucket_hour < events_before
        ][:limit]
        self.data.events = [(i, f) for i, f in self.data.events if i not in old]
        stale = [c for c in self.data.cells if c.bucket_hour < hours_before][:limit]
        self.data.cells = [c for c in self.data.cells if c not in stale]
        return UsagePruned(len(old), len(stale))


class InMemoryUsageUnitOfWork:
    """The ``UsageUnitOfWork`` over one shared ``UsageData``; a failing block is not rolled back."""

    def __init__(self, data: UsageData | None = None) -> None:
        self.data = data or UsageData()

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[InMemoryUsageStore]:
        yield InMemoryUsageStore(self.data)

    @asynccontextmanager
    async def read(self) -> AsyncIterator[InMemoryUsageStore]:
        yield InMemoryUsageStore(self.data)
