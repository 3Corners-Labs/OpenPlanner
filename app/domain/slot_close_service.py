"""The worker's closing of slots that have ended (ADR 0117 step 3, metrics C1 to C5, U2, U3, Q6).

    due? ─► slots that ended after the watermark and at least ``CLOSE_DELAY`` ago
         ─► per slot: ``slot_close.closing_facts`` ─► ``record_usage``
         ─► move the watermark                        (one transaction per pass)

The facts and the watermark share a transaction, so a crash neither writes a slot's facts twice
nor loses them, and a second pass finds nothing after the watermark (idempotent). The first
pass after the upgrade only sets the watermark: slots that ended before it are not closed
afterwards, because what the call looked like then is not known. A failure propagates to the
worker's wrapper (``app.jobs.usage``), which logs the error type and goes on.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.agenda import ShortfallAcceptance, agenda_day
from app.domain.availability import utc_bounds
from app.domain.booking import BookingKind, BookingStatus
from app.domain.closure import Closure
from app.domain.holiday_service import effective_schedule
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, TimeWindow, windows_on
from app.domain.slot_close import (
    BATCH_SLOTS,
    CLOSE_DELAY,
    EndedSlot,
    SlotStaffing,
    closing_facts,
    in_order,
)


@dataclass(frozen=True)
class SlotsClosed:
    """What one pass did: counts only.

    Attributes:
        slots: Slots closed.
        facts: Facts offered to the store (it may drop one; ``usage.fact_dropped`` says so).
        more: Whether slots are left for the next pass.
    """

    slots: int = 0
    facts: int = 0
    more: bool = False


class SlotCloser:
    """Closes ended slots, at most once per interval.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        interval: ``reporting_rollup_interval_minutes`` as a period (class C, read each time):
            the closing runs on the rollup's cycle, so it adds no setting.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, interval: Callable[[], timedelta]) -> None:
        """Store the collaborators; nothing has run yet."""
        self._uow = uow
        self._clock = clock
        self._interval = interval
        self._ran_at: datetime | None = None

    async def run_if_due(self) -> SlotsClosed | None:
        """Run when the interval has passed (or a full pass left work); ``None`` when not due.

        Any failure propagates; the interval counts from the attempt.
        """
        now = self._clock.now()
        if self._ran_at is not None and now - self._ran_at < self._interval():
            return None
        self._ran_at = now
        result = await self.run(now)
        if result.more:
            self._ran_at = None
        return result

    async def run(self, now: datetime) -> SlotsClosed:
        """Close one batch of the slots that ended before ``now - CLOSE_DELAY``."""
        until = now - CLOSE_DELAY
        async with self._uow.locked() as tx:
            after = await tx.slots_closed_until()
            if after is None:  # the upgrade: start from here, never from an unknown past
                await tx.set_slots_closed_until(until)
                return SlotsClosed()
            ended = await tx.ended_slots(after, until, BATCH_SLOTS)
            staffing = _Staffing(tx, now)
            facts = 0
            for slot in in_order(ended.slots):
                group = await staffing.group(slot.group_id)
                if group is None:
                    continue
                found = await staffing.of(group, slot)
                for fact in closing_facts(
                    slot,
                    group.organisation_id,
                    group.dispatch_kind,
                    timedelta(minutes=group.not_picked_up_minutes),
                    found,
                ):
                    await tx.record_usage(fact)
                    facts += 1
            await tx.set_slots_closed_until(ended.covered_until)
        return SlotsClosed(len(ended.slots), facts, len(ended.slots) >= BATCH_SLOTS)


class _Staffing:
    """Looks up groups, windows and closures once per pass, for the slots it closes.

    The agenda helpers of ``agenda_service`` load every booking of the day with its contact
    details; the closing needs none of them (the bookings of a slot are counted from what
    ``ended_slots`` returned), so it takes the staffing pieces and not the agenda.
    """

    def __init__(self, tx: Transaction, now: datetime) -> None:
        """Remember the transaction and the moment; nothing is read yet."""
        self._tx = tx
        self._now = now
        self._groups: dict[int, Group | None] = {}
        self._closures: dict[int, list[Closure]] = {}
        self._days: dict[
            tuple[int, date], tuple[list[TimeWindow], dict[datetime, ShortfallAcceptance]]
        ] = {}

    async def group(self, group_id: int) -> Group | None:
        """Return the group, or ``None`` if it is gone."""
        if group_id not in self._groups:
            self._groups[group_id] = await self._tx.get_group_by_id(group_id)
        return self._groups[group_id]

    async def of(self, group: Group, slot: EndedSlot) -> SlotStaffing | None:
        """Return the staffing of a scheduled ``slot`` as the agenda shows it, or ``None``.

        A pool slot has none here: its supply is the pool's, not the service's agenda.
        """
        if slot.kind is not BookingKind.SCHEDULED or slot.pooled:
            return None
        day = slot.start.astimezone(group.tz).date()
        if group.id not in self._closures:
            self._closures[group.id] = await self._tx.get_closures(group.id, self._now)
        if (group.id, day) not in self._days:
            rules, overrides = await effective_schedule(self._tx, group, day, day)
            lower, upper = utc_bounds(group, day, day)
            accepted = {
                a.start: a for a in await self._tx.get_shortfall_acceptances(group.id, lower, upper)
            }
            self._days[(group.id, day)] = (windows_on(day, rules, overrides), accepted)
        windows, accepted = self._days[(group.id, day)]
        booked = sum(1 for b in slot.bookings if b.status is BookingStatus.CONFIRMED)
        shown = agenda_day(
            group, day, windows, {slot.start: booked}, self._closures[group.id], accepted
        )
        found = next((s for s in shown if s.start == slot.start), None)
        return None if found is None else SlotStaffing(found.agents, found.booked, found.accepted)
