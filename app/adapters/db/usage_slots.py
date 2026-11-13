"""SQL for closing slots that have ended, for the reports (ADR 0117 step 3).

A mixin of ``SqlTransaction``. It reads slots with their bookings' status, language, creation
time and claim flag, and the call status of each: not one of the columns the purge empties
(``privacy.PURGED_FIELDS``; a test checks it), so a closing works the same before and after the
purge. A service's own slots and the slots of a counted pool (ADR 0122) are both closed. The
domain (``app.domain.slot_close``) decides what the slots become, a pool slot by its own rule;
the watermark that says which slots were closed lives in ``usage_rollup_state`` next to the
rollup's.
"""

from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.call_status_rows import call_status_of
from app.adapters.db.models import (
    BookingRow,
    CallStatusRow,
    ServiceRequestRow,
    SlotRow,
    UsageRollupStateRow,
    WorkItemRow,
)
from app.domain.booking import BookingKind, BookingStatus
from app.domain.calls import new_status
from app.domain.slot_close import ClosedBooking, EndedSlot, EndedSlots

_STATE_ID = 1

CLOSING_COLUMNS = ("slot_id", "status", "locale", "created_at", "claimed_at", "id")
"""The ``bookings`` columns the closing reads: none of ``privacy.PURGED_FIELDS``."""


class UsageSlotQueries:
    """The slot closing's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def ended_slots(self, after: datetime, until: datetime, limit: int) -> EndedSlots:
        """Return the slots ending in (``after``, ``until``] with their bookings, oldest first.

        Every slot: the service's own and those of a counted pool (``pool_id`` set).
        """
        window = (SlotRow.end > after, SlotRow.end <= until)
        rows = list(
            await self._session.scalars(
                select(SlotRow).where(*window).order_by(SlotRow.end, SlotRow.id).limit(limit)
            )
        )
        covered = until
        if len(rows) >= limit:
            # Never half of the slots that end together: take them all, then stop there.
            covered = rows[-1].end
            rows = list(
                await self._session.scalars(
                    select(SlotRow)
                    .where(SlotRow.end > after, SlotRow.end <= covered)
                    .order_by(SlotRow.end, SlotRow.id)
                )
            )
        by_slot = await self._bookings_of([r.id for r in rows])
        slots = tuple(
            EndedSlot(
                r.id,
                r.group_id,
                BookingKind(r.kind),
                r.start,
                r.end,
                r.capacity_snapshot,
                tuple(by_slot.get(r.id, ())),
                r.capacity_pool_id,
            )
            for r in rows
        )
        return EndedSlots(slots, covered)

    async def _bookings_of(self, slot_ids: list[int]) -> dict[int, list[ClosedBooking]]:
        """Return the closing view of every booking in ``slot_ids``, by slot.

        A booking was picked up when it was claimed in the own queue or its request has work
        that started (a contact-center attempt that connected, among others).
        """
        found: dict[int, list[ClosedBooking]] = {}
        if not slot_ids:
            return found
        started = exists(
            select(WorkItemRow.id)
            .join(ServiceRequestRow, ServiceRequestRow.id == WorkItemRow.request_id)
            .where(
                ServiceRequestRow.booking_id == BookingRow.id,
                WorkItemRow.started_at.is_not(None),
            )
        )
        result = await self._session.execute(
            select(
                BookingRow.slot_id,
                BookingRow.status,
                BookingRow.locale,
                BookingRow.created_at,
                BookingRow.claimed_at,
                BookingRow.id,
                started.label("started"),
                SlotRow.start,
                CallStatusRow,
            )
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .outerjoin(CallStatusRow, CallStatusRow.booking_id == BookingRow.id)
            .where(BookingRow.slot_id.in_(slot_ids))
            .order_by(BookingRow.id)
        )
        for slot_id, status, locale, created_at, claimed, booking_id, began, start, call in result:
            found.setdefault(slot_id, []).append(
                ClosedBooking(
                    BookingStatus(status),
                    locale,
                    created_at,
                    start,
                    claimed is not None or bool(began),
                    call_status_of(call) if call is not None else new_status(booking_id),
                )
            )
        return found

    async def slots_closed_until(self) -> datetime | None:
        """Return the end of the last slot whose closing facts were written."""
        state = await self._session.get(UsageRollupStateRow, _STATE_ID)
        return state.slots_closed_until if state is not None else None

    async def set_slots_closed_until(self, until: datetime) -> None:
        """Move the watermark of the slot closing forwards only."""
        state = await self._session.get(UsageRollupStateRow, _STATE_ID)
        if state is None:
            state = UsageRollupStateRow(id=_STATE_ID, last_event_id=0)
            self._session.add(state)
        if state.slots_closed_until is None or until > state.slots_closed_until:
            state.slots_closed_until = until
        await self._session.flush()
