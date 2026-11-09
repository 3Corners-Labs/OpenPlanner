"""SQL for rolling usage facts into the hourly table and pruning them (ADR 0117 point 4).

A mixin of ``SqlTransaction``, so every method runs in the caller's transaction: the cells and
the watermark that moves past the facts they came from are stored together, which is what makes
a crash neither double-count nor lose a fact. Nothing here reads a personal data column; the
tables hold none. The domain (``app.domain.usage_rollup``) decides what a fact becomes; the SQL
only reads, adds and deletes.
"""

from collections.abc import Sequence
from datetime import datetime

import structlog
from sqlalchemy import and_, delete, exists, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.adapters.db.models import (
    BookingRow,
    GroupRow,
    SlotRow,
    UsageBackfillRow,
    UsageEventRow,
    UsageHourRow,
    UsageRollupStateRow,
    UsageStaffHourRow,
)
from app.adapters.db.types import plain
from app.adapters.db.usage_rows import enum_of
from app.domain.booking import BookingKind, BookingStatus
from app.domain.channels import ChannelKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import (
    FACT_HOUR,
    ActorClass,
    FactKind,
    HourSource,
    StaffFact,
    UsageCell,
    UsageFact,
    UsageOutcome,
)
from app.domain.usage_backfill import BackfillBooking
from app.domain.usage_rollup import UsageBatch, UsagePruned, UsageState

log = structlog.get_logger()

_STATE_ID = 1

WRITE_KEYED = tuple(
    kind for kind, source in FACT_HOUR.items() if source is not HourSource.SLOT_START
)
"""The fact kinds whose hour is the hour they were written in (not a slot's start)."""

BACKFILL_COLUMNS = (
    "id",
    "group_id",
    "status",
    "locale",
    "created_at",
    "cancelled_at",
    "closure_id",
    "change_reason",
    "rescheduled_from_id",
    "confirm_by",
    "confirmed_at",
)
"""The ``bookings`` columns the backfill reads: none of ``privacy.PURGED_FIELDS`` (a test checks
it). The slot's start and kind and the group's organisation and dispatch kind come from their
own tables."""

_CUTS = (
    "booking_kind",
    "dispatch_kind",
    "subject_code",
    "locale",
    "channel_kind",
    "outcome",
    "actor_class",
    "reason_code",
    "bucket",
)
"""The cut columns of a cell, compared with ``IS`` so an empty cut matches an empty cut."""


def _fact_of(row: UsageEventRow) -> UsageFact | StaffFact:
    """Rebuild the fact a row holds; a value this version does not know raises ``ValueError``."""
    kind = FactKind(row.kind)
    if row.staff_id is not None:
        return StaffFact(
            kind,
            row.bucket_hour,
            row.organisation_id,
            row.group_id,
            row.staff_id,
            row.value_seconds,
        )
    return UsageFact(
        kind,
        row.bucket_hour,
        row.organisation_id,
        row.group_id,
        booking_kind=enum_of(BookingKind, row.booking_kind),
        dispatch_kind=enum_of(DispatchKind, row.dispatch_kind),
        subject_code=row.subject_code,
        locale=row.locale,
        channel_kind=enum_of(ChannelKind, row.channel_kind),
        outcome=enum_of(UsageOutcome, row.outcome),
        actor_class=enum_of(ActorClass, row.actor_class),
        reason_code=row.reason_code,
        value_seconds=row.value_seconds,
        participants=row.participants,
        capacity=row.capacity,
        booked=row.booked,
    )


class UsageRollupQueries:
    """The rollup's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def _state(self) -> UsageRollupStateRow:
        """Return the one state row, creating it empty the first time."""
        state = await self._session.get(UsageRollupStateRow, _STATE_ID)
        if state is None:
            state = UsageRollupStateRow(id=_STATE_ID, last_event_id=0)
            self._session.add(state)
        return state

    async def read_usage_events(self, after_id: int, limit: int) -> UsageBatch:
        """Return up to ``limit`` facts after ``after_id``; a row of a newer version is skipped."""
        rows = list(
            await self._session.scalars(
                select(UsageEventRow)
                .where(UsageEventRow.id > after_id)
                .order_by(UsageEventRow.id)
                .limit(limit)
            )
        )
        facts: list[UsageFact | StaffFact] = []
        unreadable = 0
        for row in rows:
            try:
                facts.append(_fact_of(row))
            except ValueError:
                unreadable += 1
        return UsageBatch(tuple(facts), rows[-1].id if rows else after_id, unreadable)

    async def usage_state(self) -> UsageState:
        """Return the watermark, the time of the last run and how many facts wait."""
        state = await self._session.get(UsageRollupStateRow, _STATE_ID)
        last = state.last_event_id if state is not None else 0
        pending = await self._session.scalar(
            select(func.count()).select_from(UsageEventRow).where(UsageEventRow.id > last)
        )
        return UsageState(
            last,
            state.rolled_at if state is not None else None,
            pending or 0,
            state.backfilled_at if state is not None else None,
        )

    async def add_usage_cells(self, cells: Sequence[UsageCell]) -> None:
        """Add each cell to the row of its hour and cut, or insert it as a new row.

        There is no unique index over the cut columns (a NULL is never equal to a NULL in
        one): the rollup is the only writer, under the write lock, so a read then a write is
        enough.
        """
        for cell in cells:
            same = and_(
                UsageHourRow.bucket_hour == cell.bucket_hour,
                UsageHourRow.organisation_id == cell.organisation_id,
                UsageHourRow.group_id == cell.group_id,
                UsageHourRow.metric == cell.metric,
                *(
                    getattr(UsageHourRow, name).is_(None)
                    if getattr(cell, name) is None
                    else getattr(UsageHourRow, name) == plain(getattr(cell, name))
                    for name in _CUTS
                ),
            )
            row = await self._session.scalar(select(UsageHourRow).where(same))
            if row is None:
                self._session.add(
                    UsageHourRow(
                        bucket_hour=cell.bucket_hour,
                        organisation_id=cell.organisation_id,
                        group_id=cell.group_id,
                        metric=cell.metric,
                        count=cell.count,
                        sum_value=cell.sum_value,
                        max_value=cell.max_value,
                        **{name: plain(getattr(cell, name)) for name in _CUTS},
                    )
                )
            else:
                row.count += cell.count
                row.sum_value += cell.sum_value
                row.max_value = max(row.max_value, cell.max_value)
        await self._session.flush()

    async def advance_usage_watermark(self, last_event_id: int, at: datetime) -> None:
        """Move the watermark and the time of the last run, forwards only."""
        state = await self._state()
        state.last_event_id = max(state.last_event_id, last_event_id)
        state.rolled_at = at
        await self._session.flush()

    async def prune_usage(
        self, events_before: datetime, hours_before: datetime, limit: int
    ) -> UsagePruned:
        """Delete at most ``limit`` rolled-up facts and ``limit`` hourly rows past their term.

        A fact the rollup has not read is never deleted, however old. The caller repeats until
        a call deletes less than ``limit``, so one transaction never holds the write lock for a
        large delete.
        """
        state = await self._session.get(UsageRollupStateRow, _STATE_ID)
        last = state.last_event_id if state is not None else 0
        old_events = (
            select(UsageEventRow.id)
            .where(UsageEventRow.id <= last, UsageEventRow.bucket_hour < events_before)
            .limit(limit)
        )
        old_hours = (
            select(UsageHourRow.id).where(UsageHourRow.bucket_hour < hours_before).limit(limit)
        )
        old_staff = (
            select(UsageStaffHourRow.id)
            .where(UsageStaffHourRow.bucket_hour < hours_before)
            .limit(limit)
        )
        await self._session.execute(
            delete(UsageStaffHourRow).where(UsageStaffHourRow.id.in_(old_staff))
        )
        events = await self._session.execute(
            delete(UsageEventRow).where(UsageEventRow.id.in_(old_events))
        )
        hours = await self._session.execute(
            delete(UsageHourRow).where(UsageHourRow.id.in_(old_hours))
        )
        return UsagePruned(events.rowcount or 0, hours.rowcount or 0)  # type: ignore[attr-defined]

    async def find_unbackfilled(self, after_id: int, limit: int) -> list[BackfillBooking]:
        """Return up to ``limit`` booking rows after ``after_id`` that are not yet facts.

        Reads only ``BACKFILL_COLUMNS`` of the booking, plus its slot and group; never a
        purgeable column. ``was_moved`` says whether another booking replaced this one.
        """
        later = aliased(BookingRow)
        moved = exists(select(later.id).where(later.rescheduled_from_id == BookingRow.id))
        rows = await self._session.execute(
            select(
                *(getattr(BookingRow, name) for name in BACKFILL_COLUMNS),
                SlotRow.start,
                SlotRow.kind,
                GroupRow.organisation_id,
                GroupRow.dispatch_kind,
                moved.label("was_moved"),
            )
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .join(GroupRow, GroupRow.id == BookingRow.group_id)
            .outerjoin(UsageBackfillRow, UsageBackfillRow.booking_id == BookingRow.id)
            .where(BookingRow.id > after_id, UsageBackfillRow.booking_id.is_(None))
            .order_by(BookingRow.id)
            .limit(limit)
        )
        return [
            BackfillBooking(
                id=r.id,
                organisation_id=r.organisation_id,
                group_id=r.group_id,
                dispatch_kind=DispatchKind(r.dispatch_kind),
                kind=BookingKind(r.kind),
                locale=r.locale,
                status=BookingStatus(r.status),
                created_at=r.created_at,
                start=r.start,
                confirmed_at=r.confirmed_at,
                confirm_by=r.confirm_by,
                cancelled_at=r.cancelled_at,
                closure_id=r.closure_id,
                change_reason=r.change_reason,
                rescheduled_from_id=r.rescheduled_from_id,
                was_moved=bool(r.was_moved),
            )
            for r in rows
        ]

    async def mark_backfilled(self, booking_ids: Sequence[int]) -> None:
        """Record bookings as turned into facts."""
        if booking_ids:
            await self._session.execute(
                insert(UsageBackfillRow), [{"booking_id": i} for i in booking_ids]
            )

    async def complete_backfill(self, at: datetime) -> None:
        """Record that every booking has been looked at."""
        (await self._state()).backfilled_at = at
        await self._session.flush()

    async def usage_cutoff(self, now: datetime) -> datetime:
        """Return the hour live facts start from, decided once and kept.

        The hour of the first fact keyed on the moment it was written (a booking created,
        confirmed, moved, suspended or resumed), or the hour of ``now`` when there is none yet;
        a fact keyed on a slot start says nothing about when it was written. It is stored with
        the first backfill, so a later run uses the same hour although the backfill has by then
        written facts of older hours.
        """
        state = await self._state()
        if state.backfill_cutoff is None:
            first = await self._session.scalar(
                select(func.min(UsageEventRow.bucket_hour)).where(
                    UsageEventRow.kind.in_([k.value for k in WRITE_KEYED])
                )
            )
            state.backfill_cutoff = (
                first if first is not None else now.replace(minute=0, second=0, microsecond=0)
            )
            await self._session.flush()
        return state.backfill_cutoff
