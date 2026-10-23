"""Ports: Protocols for everything outside the domain.

Adapters in ``app.adapters`` implement them; tests use the fakes in ``tests/fakes``.
External systems still to come with their work package: ``VideoProvider`` (WP5, including
live meeting status per ADR 0010),
``ContactCenterDispatcher`` (WP6, ADR 0007), ``MessageSender`` (WP4, ADR 0009),
``IdentityProvider`` (WP7).
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from typing import Protocol

from app.domain.booking import Booking, Slot, SlotUsage
from app.domain.channels import ReminderRule
from app.domain.job_plan import PlannedJob
from app.domain.scheduling import Group, ScheduleOverride, ScheduleRule


class Clock(Protocol):
    """Source of the current time. Injected so that time-based rules are testable."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...


class Transaction(Protocol):
    """Everything the domain reads and writes within one database transaction."""

    async def get_group(self, slug: str) -> Group | None:
        """Return the group with ``slug``."""
        ...

    async def get_group_by_id(self, group_id: int) -> Group | None:
        """Return the group with ``group_id``."""
        ...

    async def get_schedule(
        self, group_id: int, first_day: date, last_day: date
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        """Return all rules of the group and its overrides between the two dates."""
        ...

    async def get_reminder_rules(self, group_id: int) -> list[ReminderRule]:
        """Return the group's reminder rules."""
        ...

    async def get_slot_usage(
        self, group_id: int, lower: datetime, upper: datetime
    ) -> list[SlotUsage]:
        """Return capacity and active bookings of existing slots with lower <= start < upper."""
        ...

    async def get_slot(self, group_id: int, start: datetime) -> Slot | None:
        """Return the existing slot of the group at ``start``."""
        ...

    async def add_slot(self, slot: Slot) -> Slot:
        """Store a new slot and return it with its ID."""
        ...

    async def count_active_bookings(self, slot_id: int) -> int:
        """Return the number of confirmed bookings in the slot."""
        ...

    async def add_booking(self, booking: Booking) -> Booking:
        """Store a new booking and return it with its ID."""
        ...

    async def get_booking(self, booking_id: int) -> Booking | None:
        """Return the booking with ``booking_id``."""
        ...

    async def purge_personal_data(self, booking_id: int, now: datetime) -> None:
        """Set every PII column of the booking to NULL and record ``pii_purged_at``."""
        ...

    async def get_booking_by_token_hash(self, token_hash: str) -> Booking | None:
        """Return the booking whose management token hashes to ``token_hash``."""
        ...

    async def save_booking(self, booking: Booking) -> None:
        """Persist changes to an existing booking (status, cancelled_at)."""
        ...

    async def replace_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        """Delete the booking's pending jobs and store ``jobs`` instead (CLAUDE.md)."""
        ...


class UnitOfWork(Protocol):
    """Opens transactions. Leaving the context commits; an exception rolls back."""

    def read(self) -> AbstractAsyncContextManager[Transaction]:
        """Open a read transaction; it never blocks or is blocked by writers."""
        ...

    def locked(self) -> AbstractAsyncContextManager[Transaction]:
        """Open a write transaction holding the database write lock from the start.

        On SQLite this is ``BEGIN IMMEDIATE``: two bookings can never both see the last
        free place. Raises ``DomainError(database_busy)`` if the lock cannot be obtained.
        """
        ...
