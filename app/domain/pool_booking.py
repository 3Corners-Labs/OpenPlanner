"""Where an appointment is placed: the service's own staffing or a counted pool (ADR 0122 step 5).

A ``Placement`` answers two questions for one booking decision: which times are free, and which
slot takes the booking. There are two: ``LegacyPlacement``, the service's own staffing (the
code of ``BookingService``, unchanged), and ``PoolPlacement``, a counted pool. ``Placements``
picks one, and is the only place that does:

    new booking or its times ─► Placements.for_new / for_resolved ─► the flow (gate applied)
    existing booking         ─► Placements.of_booking             ─► the booking's own slot

A booking that exists is judged by its slot (its pool and length were fixed when the slot was
made); a move never crosses from one model to the other, whatever the rollback gate says.

On a pool the start times are the pool's own grid (decision P6): from the start of every run of
touching blocks that offer appointments (P7), one after another in steps of the appointment
length plus the organisation's wrap-up (``treatment_buffer_minutes``). A start is offered, and
booked, only when ``admit_appointment`` admits it against the pool's *live* staffing and every
commitment that overlaps it, of any service of the organisation (P3). Subjects do not narrow a
pool's times: its blocks carry none (P7).

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
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

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
from app.domain.flow_resolution import ResolvedFlow, pool_flows_bookable
from app.domain.flow_resolver import resolve_flow
from app.domain.holidays import closures
from app.domain.pool_accounting import (
    AdmissionOutcome,
    StartCheck,
    admit_appointment,
    check_start,
    interval_end,
)
from app.domain.pool_availability import PoolScene, PoolTarget, resolved_target, slot_target
from app.domain.pool_staffing import (
    Intake,
    PoolStaffing,
    Supply,
    lowest_units,
    supply_between,
    with_closed_days,
)
from app.domain.ports import Clock, Transaction
from app.domain.routing import RoutingChoice
from app.domain.scheduling import Group, windows_on
from app.domain.service_flows import IntakeMode
from app.domain.tunable import value_for


class Placement(Protocol):
    """Where the bookings of one decision go: free times, and the slot that takes one."""

    async def free(
        self,
        tx: Transaction,
        group: Group,
        first_day: date,
        last_day: date,
        subject: str | None,
    ) -> list[AvailableSlot]:
        """Return the bookable slots between two local dates of ``group``."""
        ...

    async def reserve(
        self, tx: Transaction, group: Group, start: datetime, routing: RoutingChoice | None
    ) -> Slot:
        """Return the slot at ``start`` with a place for one more booking (inside ``locked()``)."""
        ...


@dataclass(frozen=True)
class LegacyPlacement:
    """The service's own staffing: ``BookingService``'s own free slots and reservation."""

    free_slots: Callable[
        [Transaction, Group, date, date, str | None], Awaitable[list[AvailableSlot]]
    ]
    reserve_slot: Callable[[Transaction, Group, datetime, RoutingChoice | None], Awaitable[Slot]]

    async def free(
        self,
        tx: Transaction,
        group: Group,
        first_day: date,
        last_day: date,
        subject: str | None,
    ) -> list[AvailableSlot]:
        """Return the service's own free slots (for ``subject``, ADR 0081)."""
        return await self.free_slots(tx, group, first_day, last_day, subject)

    async def reserve(
        self, tx: Transaction, group: Group, start: datetime, routing: RoutingChoice | None
    ) -> Slot:
        """Reserve a place in the service's own slot at ``start``."""
        return await self.reserve_slot(tx, group, start, routing)


@dataclass(frozen=True)
class _Pool:
    """A pool loaded for one decision: its staffing, its closed days and the wrap-up."""

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


class PoolBookings:
    """Offer and place appointments on a counted pool (the pool side of ``BookingService``).

    Args:
        clock: Current time.
        treatment_buffer: The organisation's wrap-up in minutes (class D setting
            ``treatment_buffer_minutes``), per organisation id when it is a function; read at
            every admission and fixed into the slot's ``occupied_until``.
    """

    def __init__(self, clock: Clock, *, treatment_buffer: int | Callable[[int], int]) -> None:
        """Store the collaborators."""
        self.clock = clock
        self._treatment_buffer = treatment_buffer

    async def scene(
        self,
        tx: Transaction,
        group: Group,
        target: PoolTarget,
        lower: datetime,
        upper: datetime,
    ) -> PoolScene | None:
        """Load what availability needs for starts in ``lower`` to ``upper``, in one go.

        Only starts inside the service's booking window and outside its closures are kept;
        ``None`` when the pool offers nothing (not a counted pool of the service's organisation,
        archived, or without a time zone).
        """
        now = self.clock.now()
        earliest, latest = booking_window(group, now)
        lower, upper = max(lower, earliest), min(upper, latest + timedelta(microseconds=1))
        if lower >= upper:
            return None
        loaded = await self._load(tx, group, target, lower, upper)
        if loaded is None:
            return None
        shut = await tx.get_closures(group.id, now)
        starts = [
            start for start in loaded.starts(target, lower, upper) if covering(shut, start) is None
        ]
        if not starts:
            return None
        reach = starts[-1] + loaded.step(target)
        commitments = await tx.get_pool_commitments(loaded.id, starts[0], reach)
        supply = loaded.supply(starts[0], reach)
        return PoolScene(target, loaded.buffer_minutes, starts, supply, commitments)

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
        service's.

        Raises:
            DomainError: ``invalid_range``.
        """
        validate_range(first_day, last_day)
        lower, upper = utc_bounds(group, first_day, last_day)
        found = await self.scene(tx, group, target, lower, upper)
        return [] if found is None else found.offered(lower, upper)

    async def reserve(
        self, tx: Transaction, group: Group, target: PoolTarget, start: datetime
    ) -> Slot:
        """Return the pool slot at ``start`` with a unit admitted for it, creating it if needed.

        Must run inside ``locked()``: the commitments are read under the write lock, so two
        bookings racing for the last unit are serialised and the second is refused. The checks
        are those of a service's own slot, in the same order: the booking window, the start on
        the pool's grid, a closure of the service, room (here: the admission sweep).

        Raises:
            DomainError: ``group_not_found``, ``outside_booking_window``, ``slot_unavailable``
                (not on the grid, the pool not staffed over the whole interval, or not a counted
                pool of the organisation), ``group_closed`` or ``slot_full``.
            ValueError: If ``start`` is naive.
        """
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        if group.is_archived:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        now = self.clock.now()
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
        if not admission.admitted:  # not staffed cannot happen on the grid; kept as a guard
            staffed = admission.outcome is not AdmissionOutcome.NOT_STAFFED
            raise DomainError(ErrorCode.SLOT_FULL if staffed else ErrorCode.SLOT_UNAVAILABLE)
        slot = await tx.get_pool_slot(loaded.id, group.id, start, until)
        if slot is not None:
            return slot
        units = lowest_units(supply, start, until, Intake.APPOINTMENT)
        end = start + timedelta(minutes=target.duration_minutes)
        return await tx.add_slot(
            Slot(None, group.id, start, end, units, BookingKind.SCHEDULED, loaded.id, until)
        )

    async def check_start_of(
        self, tx: Transaction, group: Group, slot: Slot, booking_id: int, now: datetime
    ) -> StartCheck:
        """Judge whether the appointment ``booking_id`` on ``slot`` may start treatment now.

        Run inside ``locked()`` at the start (ADR 0122 decision 11, A35): the pool's live
        staffing and every commitment are read now; the length and the wrap-up are the slot's,
        fixed when it was booked (A23). An archived pool still serves the appointments it has.

        Raises:
            DomainError: ``start_refused`` when the slot is not on a counted pool of the
                service's organisation or the pool has no staffing at all.
        """
        target = slot_target(slot)
        if target is None or slot.occupied_until is None:
            raise DomainError(ErrorCode.START_REFUSED)
        loaded = await self._load(tx, group, target, now, now + timedelta(days=1), archived=True)
        if loaded is None:
            raise DomainError(ErrorCode.START_REFUSED)
        buffer = (slot.occupied_until - slot.end) // timedelta(minutes=1)
        until = interval_end(now, target.duration_minutes, buffer)
        return check_start(
            loaded.supply(now, until),
            await tx.get_pool_commitments(loaded.id, now, until),
            now,
            target.duration_minutes,
            buffer,
            intake=Intake.APPOINTMENT,
            own_key=("booking", booking_id),
        )

    async def _load(
        self,
        tx: Transaction,
        group: Group,
        target: PoolTarget,
        lower: datetime,
        upper: datetime,
        *,
        archived: bool = False,
    ) -> _Pool | None:
        """Load the target's pool for the interval, or ``None`` when it offers nothing.

        The pool must be a counted pool of the service's own organisation that is not archived
        and has a time zone: a pool id is never taken from a request, but a pool of another
        organisation is refused here as well (A05). Its closed days are the holidays of the
        calendars its organisation chose; the pool's own exception on such a date wins.
        ``archived`` lets an archived pool serve the appointments it already has (a start).
        """
        pool = await tx.get_capacity_pool(PUBLIC_READ, target.pool_id)
        if (
            pool is None
            or pool.id is None
            or pool.organisation_id != group.organisation_id
            or pool.mode is not PoolMode.COUNTED
            or (pool.archived_at is not None and not archived)
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


@dataclass(frozen=True)
class PoolPlacement:
    """A counted pool: its grid and the admission sweep. Subjects do not narrow it (P7)."""

    pools: PoolBookings
    target: PoolTarget

    async def free(
        self,
        tx: Transaction,
        group: Group,
        first_day: date,
        last_day: date,
        subject: str | None,
    ) -> list[AvailableSlot]:
        """Return the pool's free starts; ``subject`` is ignored, a pool's blocks carry none."""
        return await self.pools.free_slots(tx, group, self.target, first_day, last_day)

    async def reserve(
        self, tx: Transaction, group: Group, start: datetime, routing: RoutingChoice | None
    ) -> Slot:
        """Reserve a unit of the pool at ``start``; ``routing`` does not narrow a pool."""
        return await self.pools.reserve(tx, group, self.target, start)


class Placements:
    """The one choice between the service's own staffing and a pool, for every booking path.

    Args:
        legacy: The service's own staffing.
        pools: The pool side.
    """

    def __init__(self, legacy: LegacyPlacement, pools: PoolBookings) -> None:
        """Store the two."""
        self.legacy = legacy
        self.pools = pools

    def for_resolved(self, resolved: ResolvedFlow | None) -> Placement:
        """Return the placement of a new booking under ``resolved`` (the resolver's answer).

        The resolver has applied the rollback gate already (``gate_allows``).

        Raises:
            DomainError: ``slot_unavailable`` for a pool flow without its pool or length.
        """
        target = resolved_target(resolved)
        return self.legacy if target is None else PoolPlacement(self.pools, target)

    async def for_new(self, tx: Transaction, group: Group, flow: str | None) -> Placement:
        """Return the placement a new booking of ``group`` under ``flow`` would get.

        While no pool flow can be booked (``pool_flows_bookable``) nothing is read: every flow
        that resolves then is a legacy one.

        Raises:
            DomainError: ``flow_not_available`` for a ``flow`` that does not resolve (gate open).
        """
        if not pool_flows_bookable(IntakeMode.APPOINTMENT):
            return self.legacy
        return self.for_resolved(await resolve_flow(tx, group.id, IntakeMode.APPOINTMENT, flow))

    async def window_times(
        self, tx: Transaction, group: Group, placement: Placement
    ) -> "SceneTimes | None":
        """Return a pool placement's free times loaded once for the whole booking window.

        ``None`` for the service's own staffing, which a calendar reads window by window as
        before (``OwnTimes``).
        """
        if not isinstance(placement, PoolPlacement):
            return None
        earliest, latest = booking_window(group, self.pools.clock.now())
        scene = await self.pools.scene(
            tx, group, placement.target, earliest, latest + timedelta(microseconds=1)
        )
        return SceneTimes(group, scene)

    async def of_booking(self, tx: Transaction, booking: Booking) -> Placement:
        """Return the placement of a stored booking: the one of its own slot.

        Never the flow: a booking keeps the pool and length its slot was made with, and a
        booking without a flow on the service's own slot stays there even when the service's
        default flow has since become a pool flow. Not gated: a booking on a pool is moved on
        its pool.

        Raises:
            DomainError: ``slot_unavailable`` when the booking's slot cannot be read.
        """
        slot = await tx.get_slot_by_id(booking.slot_id)
        if slot is None:
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        target = slot_target(slot)
        return self.legacy if target is None else PoolPlacement(self.pools, target)


class FreeTimes(Protocol):
    """The free times one page reads, window by window, from one source."""

    async def between(self, first_day: date, last_day: date) -> list[AvailableSlot]:
        """Return the free slots between two local dates (inclusive)."""
        ...


@dataclass(frozen=True)
class SceneTimes:
    """Free times of a pool flow read from one scene loaded for a whole booking window."""

    group: Group
    loaded: PoolScene | None

    async def between(self, first_day: date, last_day: date) -> list[AvailableSlot]:
        """Return the scene's free starts between two local dates of the service.

        Raises:
            DomainError: ``invalid_range``.
        """
        validate_range(first_day, last_day)
        if self.loaded is None:
            return []
        lower, upper = utc_bounds(self.group, first_day, last_day)
        return self.loaded.offered(lower, upper)


@dataclass(frozen=True)
class OwnTimes:
    """Free times of the service's own staffing, each window read by ``read`` as before."""

    read: Callable[[date, date], Awaitable[list[AvailableSlot]]]

    async def between(self, first_day: date, last_day: date) -> list[AvailableSlot]:
        """Return the service's own free slots between two local dates (inclusive)."""
        return await self.read(first_day, last_day)
