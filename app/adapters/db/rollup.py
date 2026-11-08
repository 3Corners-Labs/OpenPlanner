"""SQL for merging old booking rows into daily counts (ADR 0102 step 4).

A mixin of ``SqlTransaction``, so every method runs in the caller's transaction. Nothing here
selects a personal data column except to say whether one is still filled: ``find_rollable``
returns a flag, never a value. The domain (``app.domain.booking_rollup``) decides which row may
go; the SQL only narrows the candidates and does the writes.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.contact_center_rows import purge_columns
from app.adapters.db.models import (
    BookingDayCountRow,
    BookingEventRow,
    BookingRow,
    CallStatusRow,
    JobRow,
    SlotRow,
)
from app.domain.booking import BookingStatus
from app.domain.booking_rollup import DayCount, RollableBooking

_OPEN_JOBS = ("pending", "running")


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession


class RollupQueries:
    """The booking row rollup's part of ``SqlTransaction``."""

    async def find_rollable(
        self: _Transaction, purged_before: datetime, after_id: int, limit: int
    ) -> list[RollableBooking]:
        """Return up to ``limit`` rows purged before ``purged_before``, ascending by id.

        Each row says whether a column of ``privacy.PURGED_FIELDS`` is still filled and how many
        pending or running jobs it has, so the domain can refuse it.
        """
        filled = or_(*(column.is_not(None) for column in purge_columns()))
        open_jobs = (
            select(func.count(JobRow.id))
            .where(JobRow.booking_id == BookingRow.id, JobRow.status.in_(_OPEN_JOBS))
            .scalar_subquery()
        )
        rows = await self._session.execute(
            select(
                BookingRow.id,
                BookingRow.group_id,
                SlotRow.start,
                BookingRow.status,
                BookingRow.pii_purged_at,
                filled,
                open_jobs,
            )
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .where(
                BookingRow.pii_purged_at.is_not(None),
                BookingRow.pii_purged_at <= purged_before,
                BookingRow.id > after_id,
            )
            .order_by(BookingRow.id)
            .limit(limit)
        )
        return [
            RollableBooking(
                booking_id,
                group_id,
                start.astimezone(UTC).date(),
                BookingStatus(status),
                purged_at,
                bool(holds),
                int(jobs or 0),
            )
            for booking_id, group_id, start, status, purged_at, holds, jobs in rows
        ]

    async def add_day_counts(self: _Transaction, counts: Sequence[DayCount]) -> None:
        """Add ``counts`` to the stored daily counts; a day seen before is added to."""
        for c in counts:
            insert = sqlite_insert(BookingDayCountRow).values(
                group_id=c.group_id,
                day=c.day,
                status=c.status.value,
                count=c.count,
                last_purged_at=c.last_purged_at,
            )
            await self._session.execute(
                insert.on_conflict_do_update(
                    index_elements=["group_id", "day", "status"],
                    set_={
                        "count": BookingDayCountRow.count + insert.excluded.count,
                        "last_purged_at": func.max(
                            BookingDayCountRow.last_purged_at, insert.excluded.last_purged_at
                        ),
                    },
                )
            )

    async def delete_booking_rows(self: _Transaction, booking_ids: Sequence[int]) -> None:
        """Delete the rows with their events, call status and jobs (all done or failed)."""
        ids = list(booking_ids)
        if not ids:
            return
        await self._session.execute(
            delete(BookingEventRow).where(BookingEventRow.booking_id.in_(ids))
        )
        await self._session.execute(delete(CallStatusRow).where(CallStatusRow.booking_id.in_(ids)))
        await self._session.execute(
            delete(JobRow).where(JobRow.booking_id.in_(ids), JobRow.status.not_in(_OPEN_JOBS))
        )
        await self._session.execute(delete(BookingRow).where(BookingRow.id.in_(ids)))

    async def booking_totals(self: _Transaction, group_id: int) -> dict[BookingStatus, int]:
        """Return the group's bookings per status: the live rows plus the daily counts."""
        live = (
            select(BookingRow.status.label("status"), func.count().label("n"))
            .where(BookingRow.group_id == group_id)
            .group_by(BookingRow.status)
        )
        merged = select(
            BookingDayCountRow.status.label("status"), BookingDayCountRow.count.label("n")
        ).where(BookingDayCountRow.group_id == group_id)
        both = live.union_all(merged).subquery()
        rows = await self._session.execute(
            select(both.c.status, func.sum(both.c.n)).group_by(both.c.status)
        )
        return {BookingStatus(status): int(total) for status, total in rows}
