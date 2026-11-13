"""The watchdog round of requests and the deletion of their records (ADR 0121 step 5b).

Run by the worker once per ``WATCH_EVERY``. Every round, each in its own locked transaction:

    open requests ─► watchdog_outcome(request, active item, booking holds a place?, now)
                     ─► cancelled | expired | recovery_required | back to the queue (lease)
                     ─► conditional writes; the booking's claim cleared when the work ended
    pool bookings nobody claimed, past their deadline ─► an ``expired`` request (A33)
    request-level records past ``data_deletion_at`` ─► deleted (row removed, state deleted)

A conditional write that loses (a claim or a completion in between) leaves the request for the
next round. The result holds counts only; the worker logs them, never a participant or booking.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime

from app.domain.ports import Clock, UnitOfWork
from app.domain.service_requests import (
    HandlingState,
    UnclaimedBooking,
    appointment_request,
    booking_phase,
    data_deletion_at,
    is_request_work,
    watchdog_deadline,
    watchdog_outcome,
)
from app.domain.tunable import value_for


@dataclass(frozen=True)
class WatchResult:
    """What one round changed: counts only."""

    changed: int = 0
    expired_unclaimed: int = 0
    deleted: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()
    """The type names of the failures, sorted and once each; never a message."""


class RequestWatch:
    """The watchdog round of requests and the deletion of request-level records.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        unresolved_hours: ``request_unresolved_hours`` per organisation id (class D).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        unresolved_hours: int | Callable[[int], int],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._hours = unresolved_hours
        self._errors: set[str] = set()

    async def run(self) -> WatchResult:
        """Run one round; a failure of one request never stops the others."""
        now = self._clock.now()
        self._errors = set()
        async with self._uow.read() as tx:
            watched = await tx.open_request_ids()
            unclaimed = await tx.unclaimed_pool_bookings(now)
            records = await tx.requests_holding_data()
        changed = failed = 0
        for request_id in watched:
            outcome = await self._attempt(self._watch(request_id, now))
            changed += outcome == _DONE
            failed += outcome == _FAILED
        expired = 0
        for found in unclaimed:
            if watchdog_deadline(found.end, value_for(self._hours, found.organisation_id)) > now:
                continue
            outcome = await self._attempt(self._expire(found, now))
            expired += outcome == _DONE
            failed += outcome == _FAILED
        deleted = 0
        for request in records:
            moment = data_deletion_at(request)
            if moment is None or moment > now:
                continue
            outcome = await self._attempt(self._delete(request.id or 0, now))
            deleted += outcome == _DONE
            failed += outcome == _FAILED
        return WatchResult(changed, expired, deleted, failed, tuple(sorted(self._errors)))

    async def _attempt(self, step: Awaitable[bool]) -> str:
        """Run one step; return done, skipped (nothing to do or lost a race) or failed."""
        try:
            done = await step
        except Exception as exc:  # one request must not stop the round; the result counts it
            self._errors.add(type(exc).__name__)
            return _FAILED
        return _DONE if done else _SKIPPED

    async def _watch(self, request_id: int, now: datetime) -> bool:
        """Apply the watchdog's outcome to one request, under the lock."""
        async with self._uow.locked() as tx:
            request = await tx.get_service_request(request_id)
            if request is None:
                return False
            item = await tx.get_active_work_item(request_id)
            booking = (
                await tx.get_booking(request.booking_id) if request.booking_id is not None else None
            )
            phase = booking_phase(booking.status if booking is not None else None)
            deadline = (
                watchdog_deadline(booking.end, value_for(self._hours, request.organisation_id))
                if booking is not None
                else request.watchdog_at
            )
            outcome = watchdog_outcome(request, item, phase=phase, now=now, deadline=deadline)
            if outcome is None:
                if deadline == request.watchdog_at:
                    return False
                # The booking goes ahead at another time: the deadline follows it.
                moved = replace(request, watchdog_at=deadline)
                return await tx.update_service_request(moved, request.handling)
            if outcome.item is not None and item is not None:
                if not await tx.update_work_item(outcome.item, item.state):
                    return False
            if not await tx.update_service_request(outcome.request, request.handling):
                raise RuntimeError("request changed under the lock")
            if outcome.clear_claim and booking is not None and booking.claim is not None:
                await tx.release_claim(request.booking_id or 0)
            return True

    async def _expire(self, found: UnclaimedBooking, now: datetime) -> bool:
        """Record an ``expired`` request for a pool booking nobody claimed (A33)."""
        async with self._uow.locked() as tx:
            if await tx.get_request_of_booking(found.booking_id) is not None:
                return False
            revision = await tx.flow_revision(found.flow.flow_id, found.flow.revision)
            if not is_request_work(revision):
                return False  # a legacy or video flow is never request work
            await tx.add_service_request(
                appointment_request(
                    organisation_id=found.organisation_id,
                    group_id=found.group_id,
                    flow=found.flow,
                    booking_id=found.booking_id,
                    booking_end=found.end,
                    now=now,
                    unresolved_hours=value_for(self._hours, found.organisation_id),
                    handling=HandlingState.EXPIRED,
                )
            )
            return True

    async def _delete(self, request_id: int, now: datetime) -> bool:
        """Delete one request-level record (idempotent)."""
        async with self._uow.locked() as tx:
            return await tx.delete_request_personal_data(request_id, now)


_DONE = "done"
_SKIPPED = "skipped"
_FAILED = "failed"
