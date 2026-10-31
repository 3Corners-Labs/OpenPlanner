"""The rebooking assistant's algorithm: which bookings move, and to which slot (ADR 0057).

When the staffing drops, a slot can hold more confirmed bookings than there are agents (a
*shortfall*, ``agenda``). The assistant proposes a new time for the excess. This module is
the pure part: it takes the group's agenda slots and the bookings concerned and returns
proposals; the service around it loads, locks, holds and sends.

    excess of a slot    the last-made changeable bookings, as many as the open shortfall
    candidate           a slot of the same agenda with room (agents − booked), not closed,
                        that a planner may still change, within ``search_days`` of the
                        original day and ``tolerance_minutes`` of its time of day
    ranking             smallest difference in local time of day (same time first), then
                        nearest day (earlier or later), then earliest start
    assignment          one booking at a time, earliest original slot first and last-made
                        first within a slot; each proposal takes a place, so no slot is ever
                        proposed beyond its room; no candidate → ask to rebook (ADR 0044)

Times of day are compared in the group's time zone, so a proposal keeps the participant's local
time across a daylight-saving change.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.agenda import AgendaSlot
from app.domain.booking import Booking
from app.domain.scheduling import Group


@dataclass(frozen=True)
class Reach:
    """How far a proposal may lie from the original appointment.

    Attributes:
        search_days: Days before or after the original local date (``reschedule_search_days``).
        tolerance_minutes: Largest difference in local time of day
            (``reschedule_time_tolerance_minutes``); 0 allows only the same time.
    """

    search_days: int
    tolerance_minutes: int


@dataclass(frozen=True)
class Proposal:
    """The assistant's proposal for one booking.

    Attributes:
        booking_id: The booking that has to move.
        old_start: Its current start (UTC).
        new_start: The proposed slot's start (UTC), or ``None`` when no slot fits: the
            booking then falls back to "ask to rebook".
    """

    booking_id: int
    old_start: datetime
    new_start: datetime | None


def last_made(bookings: Iterable[Booking], count: int) -> list[Booking]:
    """Return the ``count`` most recently made of ``bookings``, newest first.

    Ties on ``created_at`` go to the higher id, so the choice never depends on input order.
    """
    newest_first = sorted(bookings, key=lambda b: (b.created_at, b.id or 0), reverse=True)
    return newest_first[: max(0, count)]


def may_take(group: Group, start: datetime, now: datetime) -> bool:
    """Return whether a planner may still put a booking into the slot at ``start``.

    The slot must lie beyond the planner lead and the dispatch lead, as for any planner change,
    and beyond the group's change deadline (the participant's change lead, ADR 0069), so the participant
    can still confirm or change the proposal.
    """
    lead = max(group.planner_lead, group.dispatch_lead)
    return now < start - lead and now < group.change_deadline(start)


def ranked(
    group: Group,
    old_start: datetime,
    slots: Iterable[AgendaSlot],
    reach: Reach,
    now: datetime,
) -> list[AgendaSlot]:
    """Return the slots that could take a booking now at ``old_start``, best first.

    A slot qualifies when it is not ``old_start`` itself, is not closed, has room, may still be
    taken (``may_take``) and lies within ``reach``. The order follows the module's ranking.
    """
    fitting = [
        slot
        for slot in slots
        if slot.start != old_start
        and not slot.closed
        and slot.agents > slot.booked
        and may_take(group, slot.start, now)
        and _within(group, old_start, slot.start, reach)
    ]
    return sorted(fitting, key=lambda slot: _rank(group, old_start, slot.start))


def propose(
    group: Group,
    movers: Sequence[Booking],
    slots: Iterable[AgendaSlot],
    reach: Reach,
    now: datetime,
) -> list[Proposal]:
    """Return one proposal per booking in ``movers``, without overfilling any slot.

    Args:
        group: The group of every booking and slot; its time zone sets the time of day.
        movers: The bookings that have to move (for each short slot its ``last_made``).
        slots: The group's agenda over the search period; ``booked`` counts every booking
            that holds a place, including pending confirmations and earlier proposals.
        reach: How far a proposal may lie from the original.
        now: The current moment (UTC).

    Returns:
        Proposals in assignment order: earliest original slot first, last-made first within
        a slot.
    """
    room = {slot.start: slot.agents - slot.booked for slot in slots}
    open_slots = [slot for slot in slots if room.get(slot.start, 0) > 0]
    order = sorted(movers, key=lambda b: (b.start, -b.created_at.timestamp(), -(b.id or 0)))
    proposals = []
    for booking in order:
        best = next(
            (
                slot
                for slot in ranked(group, booking.start, open_slots, reach, now)
                if room[slot.start] > 0
            ),
            None,
        )
        if best is not None:
            room[best.start] -= 1
        proposals.append(
            Proposal(_id(booking), booking.start, None if best is None else best.start)
        )
    return proposals


def _within(group: Group, old_start: datetime, start: datetime, reach: Reach) -> bool:
    """Return whether ``start`` lies within ``reach`` of ``old_start``."""
    return (
        _day_distance(group, old_start, start) <= reach.search_days
        and _time_difference(group, old_start, start) <= reach.tolerance_minutes
    )


def _rank(group: Group, old_start: datetime, start: datetime) -> tuple[int, int, datetime]:
    """Return the sort key of ``start`` as a new time for ``old_start``: lower is better."""
    return (
        _time_difference(group, old_start, start),
        _day_distance(group, old_start, start),
        start,
    )


def _time_difference(group: Group, a: datetime, b: datetime) -> int:
    """Return the difference in local time of day between ``a`` and ``b``, in minutes."""
    return abs(_minute_of_day(group, a) - _minute_of_day(group, b))


def _minute_of_day(group: Group, moment: datetime) -> int:
    """Return the minute of the local day of ``moment`` in the group's time zone."""
    local = moment.astimezone(group.tz)
    return local.hour * 60 + local.minute


def _day_distance(group: Group, a: datetime, b: datetime) -> int:
    """Return the number of local calendar days between ``a`` and ``b``."""
    return abs((b.astimezone(group.tz).date() - a.astimezone(group.tz).date()).days)


def _id(booking: Booking) -> int:
    """Return the id of a stored booking; a missing id is a programming error."""
    if booking.id is None:
        raise ValueError("booking has not been stored")
    return booking.id
