"""The work on a request in the own queue: claim, start, complete, release (ADR 0121 step 5b).

The queue's use cases call these inside their own locked transaction, after the matrix and the
skills have been judged (``QueueService``). Only a booking on a counted pool has a request;
for every other booking each function does nothing (``None``), so the legacy queue is unchanged.

    claim    ─► request of the booking (made now if it has none) ─► work item, lease
    start    ─► holder, booking confirmed, lease ─► check_start on the booking's pool ─► occupy
    complete ─► end the work item (releases the unit once) ─► request and booking completed
    release  ─► an unstarted work item ends, the request goes back to the queue

The lease and the watchdog hours are class D settings, read when the work is claimed or the
request made and fixed into the rows (``lease_until``, ``watchdog_at``).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import cast

from app.domain.booking import Booking, BookingStatus, Slot, stored_id
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef
from app.domain.pool_accounting import StartOutcome
from app.domain.pool_booking import PoolBookings
from app.domain.ports import Transaction
from app.domain.scheduling import Group
from app.domain.service_flows import FlowRevision
from app.domain.service_requests import (
    HandlingState,
    ServiceRequest,
    WorkItem,
    WorkItemState,
    appointment_request,
    check_in,
    check_startable,
    claim,
    complete,
    is_request_work,
    needs_check_in,
    release,
    start,
)
from app.domain.tunable import value_for


@dataclass(frozen=True)
class Work:
    """A request with its active work item, as the claimed page shows it."""

    request: ServiceRequest
    item: WorkItem | None


class RequestWork:
    """Claim, start, complete and release the request of a pool booking.

    Args:
        pools: Loads a pool's staffing for the start check.
        lease_minutes: ``assignment_start_lease_minutes`` per organisation id.
        unresolved_hours: ``request_unresolved_hours`` per organisation id.
    """

    def __init__(
        self,
        pools: PoolBookings,
        *,
        lease_minutes: int | Callable[[int], int],
        unresolved_hours: int | Callable[[int], int],
    ) -> None:
        """Store the collaborators."""
        self._pools = pools
        self._lease = lease_minutes
        self._hours = unresolved_hours

    async def pool_slot(self, tx: Transaction, booking: Booking) -> Slot | None:
        """Return the booking's slot when it is on a counted pool, else ``None``."""
        slot = await tx.get_slot_by_id(booking.slot_id)
        return slot if slot is not None and slot.capacity_pool_id is not None else None

    async def handles(self, tx: Transaction, booking: Booking) -> bool:
        """Return whether ``booking`` is request work: a flow without video on a counted pool.

        Every other booking (no flow, a legacy flow, a service's own slot, a video flow) is the
        queue's work as before: no request, a meeting link, no start or complete.
        """
        return await WorkJudge(tx).handles(booking)

    async def of(self, tx: Transaction, booking: Booking) -> Work | None:
        """Return the request of ``booking`` and its active work item, ``None`` without one."""
        request = await tx.get_request_of_booking(stored_id(booking))
        if request is None:
            return None
        return Work(request, await tx.get_active_work_item(request.id or 0))

    async def claimed(
        self, tx: Transaction, group: Group, booking: Booking, agent: str, now: datetime
    ) -> Work | None:
        """Open a work item for the claimed ``booking`` when it is on a pool; make its request.

        An appointment of a flow with an explicit check-in is claimed only after its check-in
        (choice I2 of ADR 0121 step 7).

        Raises:
            DomainError: ``already_claimed`` when the request has an active work item,
                ``booking_not_active`` when it is finished, ``not_checked_in``.
        """
        judge = WorkJudge(tx)
        if booking.flow is None or not await judge.handles(booking):
            return None
        if not await judge.ready(booking):
            raise DomainError(ErrorCode.NOT_CHECKED_IN)
        found = await self.request_of(tx, group, booking, now)
        active = await tx.get_active_work_item(found.id or 0)
        lease = value_for(self._lease, group.organisation_id)
        request, item = claim(found, active, now=now, agent=agent, lease_minutes=lease)
        if not await tx.update_service_request(request, found.handling):
            raise DomainError(ErrorCode.ALREADY_CLAIMED)
        return Work(request, await tx.add_work_item(item))

    async def check_in(
        self, tx: Transaction, group: Group, booking: Booking, now: datetime
    ) -> ServiceRequest:
        """Check the appointment in: its request (made now if none) records the arrival.

        Idempotent: a second check-in keeps the first time and the one request, so the
        reservation is counted once (A15). Allowed while the queue shows the booking, from
        ``queue_visible_from_minutes`` before its start until its end (choice I3).

        Raises:
            DomainError: ``booking_not_active`` for a booking that is not confirmed request
                work, ``outside_booking_window`` outside that time.
        """
        if not booking.is_active or not await self.handles(tx, booking):
            raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
        shown = booking.start - timedelta(minutes=group.queue_visible_from_minutes)
        if not shown <= now < booking.end:
            raise DomainError(ErrorCode.OUTSIDE_BOOKING_WINDOW)
        found = await self.request_of(tx, group, booking, now)
        arrived = check_in(found, now)
        if arrived is not found and not await tx.update_service_request(arrived, found.handling):
            raise DomainError(ErrorCode.ALREADY_CLAIMED)
        return arrived

    async def request_of(
        self, tx: Transaction, group: Group, booking: Booking, now: datetime
    ) -> ServiceRequest:
        """Return the request of ``booking``, made now (``queued``) when it has none.

        The one rule of a request of an appointment, for a claim in the own queue and for an
        attempt at the contact center (ADR 0121 step 6b). ``booking`` is request work.
        """
        found = await tx.get_request_of_booking(stored_id(booking))
        if found is not None:
            return found
        return await tx.add_service_request(
            appointment_request(
                organisation_id=group.organisation_id,
                group_id=group.id,
                flow=cast(FlowRef, booking.flow),
                booking_id=stored_id(booking),
                booking_end=booking.end,
                now=now,
                unresolved_hours=value_for(self._hours, group.organisation_id),
            )
        )

    async def check_start(
        self, tx: Transaction, group: Group, slot: Slot, booking_id: int, now: datetime
    ) -> StartOutcome:
        """Return how the booking's pool judges a start now (``check_start_of``)."""
        return (await self._pools.check_start_of(tx, group, slot, booking_id, now)).outcome

    async def start(self, tx: Transaction, group: Group, booking: Booking, now: datetime) -> Work:
        """Start the treatment of ``booking``'s claimed request, judged on its pool now.

        Raises:
            DomainError: ``not_claimed`` without a request, ``booking_not_active``,
                ``lease_expired``, ``request_in_progress`` or ``start_refused``.
        """
        work = await self.of(tx, booking)
        slot = await self.pool_slot(tx, booking)
        if work is None or slot is None or slot.occupied_until is None:
            raise DomainError(ErrorCode.NOT_CLAIMED)
        if not booking.is_active:
            raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
        check_startable(work.item, now)  # an expired lease says so before the pool is asked
        check = await self._pools.check_start_of(tx, group, slot, stored_id(booking), now)
        if check.outcome is StartOutcome.REFUSED:
            raise DomainError(ErrorCode.START_REFUSED)
        until = now + (slot.occupied_until - slot.start)
        request, item = start(
            work.request,
            work.item,
            now=now,
            pool_id=slot.capacity_pool_id or 0,
            planned_until=until,
            conflict=check.outcome is StartOutcome.OVERRUN_CONFLICT,
        )
        await self._write(tx, work, request, item)
        return Work(request, item)

    async def complete(self, tx: Transaction, booking: Booking, now: datetime) -> Work:
        """Complete ``booking``'s request: the unit is released and the booking completed.

        Raises:
            DomainError: ``not_claimed`` without active work, ``request_not_started``.
        """
        work = await self.of(tx, booking)
        if work is None:
            raise DomainError(ErrorCode.NOT_CLAIMED)
        request, item = complete(work.request, work.item, now=now)
        await self._write(tx, work, request, item)
        if booking.status is BookingStatus.CONFIRMED:
            await tx.save_booking(replace(booking, status=BookingStatus.COMPLETED))
        return Work(request, item)

    async def released(self, tx: Transaction, booking: Booking, now: datetime) -> None:
        """End the unstarted work item of ``booking``'s request; nothing for other bookings.

        Raises:
            DomainError: ``request_in_progress`` when treatment started.
        """
        work = await self.of(tx, booking)
        if work is None or work.item is None:
            return
        request, item = release(work.request, work.item, now=now)
        await self._write(tx, work, request, item)

    async def _write(
        self, tx: Transaction, work: Work, request: ServiceRequest, item: WorkItem
    ) -> None:
        """Write the new states of ``work`` (``write_work``)."""
        await write_work(tx, work.request, work.item, request, item)


async def write_work(
    tx: Transaction,
    seen_request: ServiceRequest,
    seen_item: WorkItem | None,
    request: ServiceRequest,
    item: WorkItem | None,
) -> None:
    """Write a request and its work item, conditional on the states read; a race loses cleanly.

    The one write of every change of request work: the queue's appointments, immediate work
    (``request_queue``) and a participant's withdrawal (``immediate_requests``).

    Raises:
        DomainError: ``already_claimed`` when another transaction changed either first.
    """
    seen = seen_item.state if seen_item is not None else WorkItemState.ASSIGNED
    written = item is None or await tx.update_work_item(item, seen)
    if not (written and await tx.update_service_request(request, seen_request.handling)):
        raise DomainError(ErrorCode.ALREADY_CLAIMED)


def recovering(work: Work | None) -> bool:
    """Return whether the work is marked for recovery (a supervisor may complete it)."""
    return work is not None and work.request.handling is HandlingState.RECOVERY_REQUIRED


class WorkJudge:
    """Whether bookings are request work, reading each slot and flow revision once.

    One per transaction: a queue view judges every row, and rows share slots and revisions,
    so a polled page reads each of them once instead of twice per row.
    """

    def __init__(self, tx: Transaction) -> None:
        """Bind to ``tx`` with empty caches."""
        self._tx = tx
        self._pooled: dict[int, bool] = {}
        self._revisions: dict[FlowRef, FlowRevision | None] = {}

    async def handles(self, booking: Booking) -> bool:
        """Return whether ``booking`` is request work (``RequestWork.handles``)."""
        if booking.flow is None:
            return False
        if booking.slot_id not in self._pooled:
            slot = await self._tx.get_slot_by_id(booking.slot_id)
            self._pooled[booking.slot_id] = slot is not None and slot.capacity_pool_id is not None
        if not self._pooled[booking.slot_id]:
            return False
        return is_request_work(await self._revision(booking.flow))

    async def needs_check_in(self, booking: Booking) -> bool:
        """Return whether ``booking`` is claimed only after a check-in (``needs_check_in``)."""
        return await self.handles(booking) and needs_check_in(
            await self._revision(cast(FlowRef, booking.flow))
        )

    async def ready(self, booking: Booking) -> bool:
        """Return whether ``booking`` may be claimed now: no check-in needed, or checked in."""
        if not await self.needs_check_in(booking):
            return True
        request = await self._tx.get_request_of_booking(stored_id(booking))
        return request is not None and request.arrived_at is not None

    async def _revision(self, ref: FlowRef) -> FlowRevision | None:
        """Return the revision ``ref``, read once per transaction."""
        if ref not in self._revisions:
            self._revisions[ref] = await self._tx.flow_revision(ref.flow_id, ref.revision)
        return self._revisions[ref]
