"""Appointments on a counted capacity pool: what is offered and how one is placed (ADR 0122 step 5).

A flow that is not the legacy wrapper of a service draws on a counted pool. Its start times are
the pool's own grid (decision P6): from the start of every run of touching blocks that offer
appointments (P7), one after another in steps of the appointment length plus the organisation's
wrap-up (``treatment_buffer_minutes``). A start is offered, and booked, only when
``admit_appointment`` admits it against the pool's *live* staffing and every commitment that
overlaps it, of any service of the organisation (P3); nothing is snapshotted for a later
booking.

    book (pool flow) ─► locked tx ─► window ─► pool of the service's organisation, staffed?
                                     ─► start on the grid? ─► closure of the service?
                                     ─► admit_appointment(live supply, commitments read now)
                                     ─► pool slot (reuse, or create with occupied_until)
                                     ─► (the caller) address limit, booking, jobs

Everything runs inside the caller's transaction; a booking reads the commitments inside the
write lock of ``locked()``, so the admission sweep and the write are one step (invariant 9). A
booking's place is derived from its status (``HOLDS_PLACE`` and open proposals): cancelling or
moving it frees the unit with no ledger to update, and a booking suspended by an emergency
closure of its service keeps its unit (decision P4).

The pool slot carries ``occupied_until`` = start + length + wrap-up, fixed when it is created;
the booking itself ends at start + length, so the deletion of its personal data (``slot.end``
plus 24 hours) is not moved by the wrap-up. A pool slot carries no personal data.

Reachable only through a flow that ``flow_resolution.gate_allows``, which holds every pool flow
back while ``ROLLBACK_SAFE`` is false, and through a move of a booking that already sits on a
pool (``target_of_booking``): such a booking stays on its pool whatever the gate says.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.authorization import PUBLIC_READ
from app.domain.availability import (
    AvailableSlot,
    booking_window,
    grid_on,
    utc_bounds,
    validate_range,
)
from app.domain.booking import Booking, BookingKind, Slot
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.closure import covering
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef, ResolvedFlow
from app.domain.holidays import closures
from app.domain.pool_accounting import (
    AdmissionOutcome,
    Commitment,
    admit_appointment,
    interval_end,
)
from app.domain.pool_staffing import (
    Intake,
    PoolStaffing,
    Supply,
    lowest_units,
    supply_between,
    with_closed_days,
)
from app.domain.ports import Clock, Transaction
from app.domain.scheduling import Group, windows_on
from app.domain.service_flows import FlowRevision
from app.domain.tunable import value_for


@dataclass(frozen=True)
class PoolTarget:
    """What an appointment of a pool flow draws on: the pool and the appointment's length."""

    pool_id: int
    duration_minutes: int


def pool_target(revision: ResolvedFlow | FlowRevision | None) -> PoolTarget | None:
    """Return what a booking under ``revision`` draws on; ``None`` for a legacy one or none.

    A revision that is not legacy always has a pool and a length once it is published (the
    readiness checklist and ``check_flow_references``); one without either is refused rather than
    placed on the service's own staffing, which would sell a unit twice.

    Raises:
        DomainError: ``slot_unavailable`` for a revision that is not legacy but lacks its pool or
            its length.
    """
    if revision is None or revision.legacy:
        return None
    if revision.capacity_pool_id is None or revision.duration_minutes is None:
        raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
    return PoolTarget(revision.capacity_pool_id, revision.duration_minutes)


@dataclass(frozen=True)
class _Pool:
    """A pool loaded for one booking decision: its staffing, its closed days and the wrap-up."""

    pool: CapacityPool
    staffing: PoolStaffing
    closed_days: frozenset[date]
    buffer_minutes: int

    @property
    def id(self) -> int:
        """The pool's id."""
        return self.pool.id or 0

    def step(self, target: PoolTarget) -> timedelta:
        """Return the grid step: the appointment's length plus the wrap-up (decision P6)."""
        return timedelta(minutes=target.duration_minutes + self.buffer_minutes)

    def local_days(self, lower: datetime, upper: datetime) -> list[date]:
        """Return the pool's local dates that the UTC interval ``lower`` to ``upper`` touches."""
        zone = self.staffing.timezone
        first = lower.astimezone(zone).date()
        last = (upper - timedelta(microseconds=1)).astimezone(zone).date()
        return [first + timedelta(days=n) for n in range((last - first).days + 1)]

    def starts(self, target: PoolTarget, lower: datetime, upper: datetime) -> list[datetime]:
        """Return the grid starts with ``lower <= start < upper``, in time order (P6, P7)."""
        overrides = with_closed_days(self.staffing.overrides, self.closed_days)
        found: list[datetime] = []
        for day in self.local_days(lower, upper):
            windows = windows_on(day, self.staffing.rules, overrides)
            grid = grid_on(self.staffing.timezone, self.step(target), day, windows)
            found += [slot.start for slot in grid if lower <= slot.start < upper]
        return sorted(found)

    def supply(self, lower: datetime, upper: datetime) -> Supply:
        """Return the staffed segments over ``lower`` to ``upper``, holidays closed."""
        return supply_between(self.staffing, self.closed_days, lower, upper)


def offered(
    pool_starts: Sequence[datetime],
    supply: Supply,
    commitments: Sequence[Commitment],
    target: PoolTarget,
    buffer_minutes: int,
) -> list[AvailableSlot]:
    """Return the starts that admission lets through, each with its places left.

    ``capacity`` is the fewest units staffed for appointments over the slot's interval,
    ``remaining`` the fewest free units (``admit_appointment``); the slot ends after the
    appointment's length, without the wrap-up.
    """
    length = timedelta(minutes=target.duration_minutes)
    result: list[AvailableSlot] = []
    for start in pool_starts:
        admission = admit_appointment(
            supply, commitments, start, target.duration_minutes, buffer_minutes
        )
        if admission.admitted:
            until = interval_end(start, target.duration_minutes, buffer_minutes)
            units = lowest_units(supply, start, until, Intake.APPOINTMENT)
            result.append(AvailableSlot(start, start + length, units, admission.free))
    return result


class PoolBookings:
    """The pool side of ``BookingService``: offer and place appointments of a pool flow.

    Args:
        clock: Current time.
        treatment_buffer: The organisation's wrap-up in minutes (class D setting
            ``treatment_buffer_minutes``), per organisation id when it is a function; read at
            every admission and fixed into the slot's ``occupied_until``.
    """

    def __init__(self, clock: Clock, treatment_buffer: int | Callable[[int], int] = 0) -> None:
        """Store the collaborators."""
        self._clock = clock
        self._treatment_buffer = treatment_buffer

    async def target_of_booking(self, tx: Transaction, booking: Booking) -> PoolTarget | None:
        """Return the pool a stored booking draws on, from the revision it was made under.

        ``None`` for a booking without a flow, under a legacy revision, or under a revision that
        cannot be read. Not gated: a booking on a pool is moved on its pool (see the module).
        """
        return await self.target_of_ref(tx, booking.flow)

    async def target_of_ref(self, tx: Transaction, ref: FlowRef | None) -> PoolTarget | None:
        """Return the pool the revision ``ref`` draws on; see ``target_of_booking``."""
        if ref is None:
            return None
        return pool_target(await tx.flow_revision(ref.flow_id, ref.revision))

    async def free_slots(
        self,
        tx: Transaction,
        group: Group,
        target: PoolTarget,
        first_day: date,
        last_day: date,
    ) -> list[AvailableSlot]:
        """Return the bookable starts of a pool flow of ``group`` between two local dates.

        The dates are the service's (the page's calendar); the grid is the pool's, in the pool's
        time zone, so a pool in another zone works. Notice, horizon and closures are the
        service's. An unstaffed pool, a pool without a time zone and one that is not a counted
        pool of the service's organisation offer nothing.

        Raises:
            DomainError: ``invalid_range``.
        """
        validate_range(first_day, last_day)
        lower, upper = utc_bounds(group, first_day, last_day)
        now = self._clock.now()
        loaded = await self._load(tx, group, target, lower, upper)
        if loaded is None:
            return []
        earliest, latest = booking_window(group, now)
        shut = await tx.get_closures(group.id, now)
        starts = [
            start
            for start in loaded.starts(target, lower, upper)
            if earliest <= start <= latest and covering(shut, start) is None
        ]
        if not starts:
            return []
        reach = starts[-1] + loaded.step(target)
        commitments = await tx.get_pool_commitments(loaded.id, starts[0], reach)
        supply = loaded.supply(starts[0], reach)
        return offered(starts, supply, commitments, target, loaded.buffer_minutes)

    async def reserve(
        self, tx: Transaction, group: Group, target: PoolTarget, start: datetime
    ) -> Slot:
        """Return the pool slot at ``start`` with a unit admitted for it, creating it if needed.

        Must run inside ``locked()``: the commitments are read under the write lock, so two
        bookings racing for the last unit are serialised and the second is refused. The checks
        are those of a service's own slot, in the same order: the booking window, the start on
        the pool's grid, a closure of the service, room (here: the admission sweep).

        Raises:
            DomainError: ``outside_booking_window``, ``slot_unavailable`` (not on the grid, the
                pool not staffed over the whole interval, or not a counted pool of the
                organisation), ``group_closed`` or ``slot_full``.
            ValueError: If ``start`` is naive.
        """
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        if group.is_archived:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        now = self._clock.now()
        earliest, latest = booking_window(group, now)
        if not earliest <= start <= latest:
            raise DomainError(ErrorCode.OUTSIDE_BOOKING_WINDOW)
        loaded = await self._load(tx, group, target, start, start + timedelta(days=1))
        if loaded is None:
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        until = interval_end(start, target.duration_minutes, loaded.buffer_minutes)
        if start not in loaded.starts(target, start, start + timedelta(microseconds=1)):
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        if covering(await tx.get_closures(group.id, now), start) is not None:
            raise DomainError(ErrorCode.GROUP_CLOSED)
        supply = loaded.supply(start, until)
        commitments = await tx.get_pool_commitments(loaded.id, start, until)
        admission = admit_appointment(
            supply, commitments, start, target.duration_minutes, loaded.buffer_minutes
        )
        if admission.outcome is AdmissionOutcome.NOT_STAFFED:
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        if not admission.admitted:
            raise DomainError(ErrorCode.SLOT_FULL)
        slot = await tx.get_pool_slot(loaded.id, group.id, start, until)
        if slot is not None:
            return slot
        units = lowest_units(supply, start, until, Intake.APPOINTMENT)
        end = start + timedelta(minutes=target.duration_minutes)
        return await tx.add_slot(
            Slot(None, group.id, start, end, units, BookingKind.SCHEDULED, loaded.id, until)
        )

    async def _load(
        self,
        tx: Transaction,
        group: Group,
        target: PoolTarget,
        lower: datetime,
        upper: datetime,
    ) -> _Pool | None:
        """Load the target's pool for the interval, or ``None`` when it offers nothing.

        The pool must be a counted pool of the service's own organisation that is not archived
        and has a time zone: a pool id is never taken from a request, but a pool of another
        organisation is refused here as well (A05). Its closed days are the holidays of the
        calendars its organisation chose; the pool's own exception on such a date wins.
        """
        pool = await tx.get_capacity_pool(PUBLIC_READ, target.pool_id)
        if (
            pool is None
            or pool.id is None
            or pool.organisation_id != group.organisation_id
            or pool.mode is not PoolMode.COUNTED
            or pool.archived_at is not None
        ):
            return None
        staffing = await tx.get_pool_staffing(PUBLIC_READ, pool.id)
        if staffing is None:
            return None
        buffer = value_for(self._treatment_buffer, group.organisation_id)
        shell = _Pool(pool, staffing, frozenset(), buffer)
        span = shell.local_days(lower, upper + shell.step(target))
        calendars = await tx.get_organisation_holiday_calendars(group.organisation_id)
        closed = (
            frozenset(
                h.day for h in closures(await tx.get_holidays(calendars, span[0], span[-1]), ())
            )
            if calendars
            else frozenset[date]()
        )
        return _Pool(pool, staffing, closed, buffer)
