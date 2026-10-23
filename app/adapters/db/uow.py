"""SQL implementation of the domain's ``UnitOfWork`` and ``Transaction`` ports.

``locked()`` starts the transaction with ``BEGIN IMMEDIATE``: SQLite takes the write lock up
front, so the capacity check and the insert of a booking can never interleave with another
writer. ``read()`` uses a deferred ``BEGIN``; in WAL mode readers never wait for writers.

All mapping between rows and domain objects lives in this module.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime

from sqlalchemy import case, delete, func, insert, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.engine import BEGIN_MODE_OPTION
from app.adapters.db.models import (
    BookingRow,
    GroupRow,
    JobRow,
    ReminderRuleRow,
    ScheduleOverrideRow,
    ScheduleRuleRow,
    SlotRow,
)
from app.domain.booking import Booking, BookingStatus, Contact, Slot, SlotUsage
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import PlannedJob
from app.domain.scheduling import (
    Group,
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
    TimeWindow,
)

_PENDING = "pending"


def _group(row: GroupRow) -> Group:
    """Map a group row to the domain."""
    return Group(
        id=row.id,
        organisation_id=row.organisation_id,
        name=row.name,
        slug=row.slug,
        timezone=row.timezone,
        slot_minutes=row.slot_minutes,
        min_notice_minutes=row.min_notice_minutes,
        horizon_days=row.horizon_days,
        phone_mode=PhoneMode(row.phone_mode),
        wxcc_queue_id=row.wxcc_queue_id,
        dispatch_lead_minutes=row.dispatch_lead_minutes,
    )


def _override(row: ScheduleOverrideRow) -> ScheduleOverride:
    """Map an override row; NULL window columns mean a closed day."""
    if row.opens is None or row.closes is None or row.capacity is None:
        return ScheduleOverride(day=row.day, window=None)
    return ScheduleOverride(day=row.day, window=TimeWindow(row.opens, row.closes, row.capacity))


def _booking(row: BookingRow, slot: SlotRow) -> Booking:
    """Map a booking row and its slot; a purged booking has no contact."""
    contact = (
        Contact(name=row.name, email=row.email, phone=row.phone)
        if row.name is not None and row.email is not None
        else None
    )
    return Booking(
        id=row.id,
        group_id=row.group_id,
        slot_id=row.slot_id,
        start=slot.start,
        end=slot.end,
        status=BookingStatus(row.status),
        contact=contact,
        locale=row.locale,
        token_hash=row.management_token_hash,
        created_at=row.created_at,
        consents=frozenset(ChannelKind(c) for c in row.consents.split(",") if c),
        cancelled_at=row.cancelled_at,
        rescheduled_from_id=row.rescheduled_from_id,
    )


class SqlTransaction:
    """``Transaction`` on an ``AsyncSession`` whose transaction is already open."""

    def __init__(self, session: AsyncSession) -> None:
        """Wrap ``session``."""
        self._session = session

    async def get_group(self, slug: str) -> Group | None:
        """Return the group with ``slug``."""
        row = await self._session.scalar(select(GroupRow).where(GroupRow.slug == slug))
        return _group(row) if row else None

    async def get_group_by_id(self, group_id: int) -> Group | None:
        """Return the group with ``group_id``."""
        row = await self._session.get(GroupRow, group_id)
        return _group(row) if row else None

    async def get_schedule(
        self, group_id: int, first_day: date, last_day: date
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        """Return all rules of the group and its overrides between the two dates."""
        rules = await self._session.scalars(
            select(ScheduleRuleRow).where(ScheduleRuleRow.group_id == group_id)
        )
        overrides = await self._session.scalars(
            select(ScheduleOverrideRow).where(
                ScheduleOverrideRow.group_id == group_id,
                ScheduleOverrideRow.day.between(first_day, last_day),
            )
        )
        return (
            [
                ScheduleRule(
                    weekday=r.weekday,
                    window=TimeWindow(r.opens, r.closes, r.capacity),
                    valid_from=r.valid_from,
                    valid_to=r.valid_to,
                )
                for r in rules
            ],
            [_override(o) for o in overrides],
        )

    async def get_reminder_rules(self, group_id: int) -> list[ReminderRule]:
        """Return the group's reminder rules."""
        rows = await self._session.scalars(
            select(ReminderRuleRow).where(ReminderRuleRow.group_id == group_id)
        )
        return [ReminderRule(ChannelKind(r.channel), r.minutes_before, r.enabled) for r in rows]

    async def get_slot_usage(
        self, group_id: int, lower: datetime, upper: datetime
    ) -> list[SlotUsage]:
        """Return capacity and active bookings of existing slots, in one query."""
        active = func.count(case((BookingRow.status == BookingStatus.CONFIRMED.value, 1)))
        rows = await self._session.execute(
            select(SlotRow.start, SlotRow.capacity_snapshot, active)
            .outerjoin(BookingRow, BookingRow.slot_id == SlotRow.id)
            .where(SlotRow.group_id == group_id, SlotRow.start >= lower, SlotRow.start < upper)
            .group_by(SlotRow.id)
        )
        return [SlotUsage(start, capacity, int(count)) for start, capacity, count in rows]

    async def get_slot(self, group_id: int, start: datetime) -> Slot | None:
        """Return the existing slot of the group at ``start``."""
        row = await self._session.scalar(
            select(SlotRow).where(SlotRow.group_id == group_id, SlotRow.start == start)
        )
        if row is None:
            return None
        return Slot(row.id, row.group_id, row.start, row.end, row.capacity_snapshot)

    async def add_slot(self, slot: Slot) -> Slot:
        """Insert a slot and return it with its ID."""
        row = SlotRow(
            group_id=slot.group_id,
            start=slot.start,
            end=slot.end,
            capacity_snapshot=slot.capacity_snapshot,
        )
        self._session.add(row)
        await self._session.flush()
        return Slot(row.id, slot.group_id, slot.start, slot.end, slot.capacity_snapshot)

    async def count_active_bookings(self, slot_id: int) -> int:
        """Return the number of confirmed bookings in the slot."""
        count = await self._session.scalar(
            select(func.count()).where(
                BookingRow.slot_id == slot_id,
                BookingRow.status == BookingStatus.CONFIRMED.value,
            )
        )
        return int(count or 0)

    async def add_booking(self, booking: Booking) -> Booking:
        """Insert a booking and return it with its ID."""
        contact = booking.contact
        row = BookingRow(
            slot_id=booking.slot_id,
            group_id=booking.group_id,
            status=booking.status.value,
            name=contact.name if contact else None,
            email=contact.email if contact else None,
            phone=contact.phone if contact else None,
            locale=booking.locale,
            consents=",".join(sorted(booking.consents)),
            management_token_hash=booking.token_hash,
            created_at=booking.created_at,
            cancelled_at=booking.cancelled_at,
            rescheduled_from_id=booking.rescheduled_from_id,
        )
        self._session.add(row)
        await self._session.flush()
        return _booking(row, await self._slot_row(row.slot_id))

    async def get_booking_by_token_hash(self, token_hash: str) -> Booking | None:
        """Return the booking whose management token hashes to ``token_hash``."""
        result = await self._session.execute(
            select(BookingRow, SlotRow)
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .where(BookingRow.management_token_hash == token_hash)
        )
        found = result.one_or_none()
        return _booking(*found) if found else None

    async def save_booking(self, booking: Booking) -> None:
        """Persist the mutable fields of a booking (status and cancellation time)."""
        await self._session.execute(
            update(BookingRow)
            .where(BookingRow.id == booking.id)
            .values(status=booking.status.value, cancelled_at=booking.cancelled_at)
        )

    async def replace_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        """Delete the booking's pending jobs and insert ``jobs``."""
        await self._session.execute(
            delete(JobRow).where(JobRow.booking_id == booking_id, JobRow.status == _PENDING)
        )
        if jobs:
            await self._session.execute(
                insert(JobRow),
                [
                    {
                        "booking_id": booking_id,
                        "type": job.type.value,
                        "run_at": job.run_at,
                        "pinned_start": job.pinned_start,
                        "channel": job.channel.value if job.channel else None,
                    }
                    for job in jobs
                ],
            )

    async def _slot_row(self, slot_id: int) -> SlotRow:
        """Return the slot row of a booking (it always exists: foreign key)."""
        row = await self._session.get(SlotRow, slot_id)
        if row is None:  # pragma: no cover - guarded by the foreign key
            raise LookupError("slot missing")
        return row


def _is_lock_timeout(exc: OperationalError) -> bool:
    """Return whether SQLite gave up waiting for the write lock."""
    message = str(exc.orig).lower()
    return "database is locked" in message or "database is busy" in message


class SqlUnitOfWork:
    """``UnitOfWork`` over an ``AsyncEngine``.

    Args:
        engine: Engine created by ``app.adapters.db.engine.create_engine``.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """Store the engine."""
        self._engine = engine

    @asynccontextmanager
    async def read(self) -> AsyncIterator[SqlTransaction]:
        """Open a read transaction (deferred ``BEGIN``)."""
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            async with session.begin():
                yield SqlTransaction(session)

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[SqlTransaction]:
        """Open a write transaction with ``BEGIN IMMEDIATE``; commit on success.

        Raises:
            DomainError: ``database_busy`` if the write lock is not obtained in time.
        """
        async with self._engine.connect() as raw:
            connection = await raw.execution_options(**{BEGIN_MODE_OPTION: "IMMEDIATE"})
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                try:
                    async with session.begin():
                        yield SqlTransaction(session)
                except OperationalError as exc:
                    if _is_lock_timeout(exc):
                        raise DomainError(ErrorCode.DATABASE_BUSY) from exc
                    raise
