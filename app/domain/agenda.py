"""A group's agenda: per slot the agents on duty and the bookings that need one (ADR 0044).

Staffing is a count of agents per block (ADR 0027) and a booking is never tied to an agent.
When the planner lowers the staffing, bookings already made stay, so a slot can hold more
confirmed bookings than there are agents: a *shortfall*. It is computed, never stored.

    effective staffing (blocks, exceptions, holidays)  ──► agents per slot (lowest block)
    closure covering the slot                           ──► 0 agents, closed
    confirmed bookings per slot start                   ──► booked
    shortfall = booked − agents, when positive

A planner can accept a shortfall (ADR 0044, "leave as is", for example when a colleague of
another team helps out). The acceptance holds the counts it was given for; as soon as the
staffing or the bookings of that slot change, it no longer matches and the shortfall shows
again.

A booking whose slot the staffing no longer covers (a block removed or shortened) needs an
agent that is not there: that slot gets 0 agents. Slots of an old time grid are counted on
their own; overlap between grids is left to the participant's availability (``availability``).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from app.domain.availability import slots_on, staffed_runs
from app.domain.closure import Closure, covering
from app.domain.scheduling import Group, TimeWindow


@dataclass(frozen=True)
class ShortfallAcceptance:
    """A planner's mark that a slot's shortfall is accepted as it was.

    Attributes:
        group_id: The group.
        start: The slot's start (UTC).
        agents: Agents at the moment of acceptance.
        booked: Confirmed bookings at the moment of acceptance.
        accepted_at: When (UTC).
        actor: Subject identifier of the planner (no personal data).
    """

    group_id: int
    start: datetime
    agents: int
    booked: int
    accepted_at: datetime
    actor: str


@dataclass(frozen=True)
class AgendaSlot:
    """One slot of the agenda.

    Attributes:
        start: Start (UTC).
        end: End (UTC).
        agents: Agents on duty for the whole slot under the current staffing.
        booked: Confirmed scheduled bookings in the slot.
        closed: Whether an emergency closure covers the slot (ADR 0024).
        accepted: Whether a planner accepted the shortfall at exactly these counts.
    """

    start: datetime
    end: datetime
    agents: int
    booked: int
    closed: bool = False
    accepted: bool = False

    @property
    def shortfall(self) -> int:
        """Return how many bookings have no agent; 0 when the slot is staffed well enough."""
        return max(0, self.booked - self.agents)

    @property
    def open_shortfall(self) -> int:
        """Return the shortfall unless a planner accepted it."""
        return 0 if self.accepted else self.shortfall

    def matches(self, acceptance: ShortfallAcceptance) -> bool:
        """Return whether ``acceptance`` was given for this slot as it is now."""
        return (acceptance.start, acceptance.agents, acceptance.booked) == (
            self.start,
            self.agents,
            self.booked,
        )


def agents_for(
    group: Group, start: datetime, end: datetime, day: date, windows: Sequence[TimeWindow]
) -> int:
    """Return the agents on duty for all of ``start``–``end`` on ``day``.

    The slot must lie inside one run of touching staffed blocks; it then gets the lowest
    count among the blocks it covers, as in the participant's availability. Otherwise 0.
    """
    tz = group.tz
    local_start = start.astimezone(tz).replace(tzinfo=None)
    local_end = end.astimezone(tz).replace(tzinfo=None)
    for run in staffed_runs(windows):
        opens = datetime.combine(day, run[0].opens)
        closes = datetime.combine(day, run[-1].closes)
        if opens <= local_start and local_end <= closes:
            return min(
                w.capacity
                for w in run
                if datetime.combine(day, w.opens) < local_end
                and local_start < datetime.combine(day, w.closes)
            )
    return 0


def agenda_day(
    group: Group,
    day: date,
    windows: Sequence[TimeWindow],
    booked: Mapping[datetime, int],
    closures: Iterable[Closure],
    acceptances: Mapping[datetime, ShortfallAcceptance] | None = None,
) -> list[AgendaSlot]:
    """Return the slots of ``day`` (group time): those the staffing offers and those booked.

    Args:
        group: The group, for time zone and slot length.
        day: Local date.
        windows: The effective staffing of ``day``.
        booked: Confirmed scheduled bookings per slot start (UTC); starts on other days are
            ignored.
        closures: The group's closures that have not ended.
        acceptances: Accepted shortfalls per slot start; only a matching one counts.
    """
    length = timedelta(minutes=group.slot_minutes)
    known = list(closures)
    starts = {s.start for s in slots_on(group, day, windows)}
    starts |= {s.astimezone(UTC) for s in booked if s.astimezone(group.tz).date() == day}
    result = []
    for start in sorted(starts):
        closed = covering(known, start) is not None
        agents = 0 if closed else agents_for(group, start, start + length, day, windows)
        slot = AgendaSlot(start, start + length, agents, booked.get(start, 0), closed)
        acceptance = (acceptances or {}).get(start)
        if slot.shortfall and acceptance is not None and slot.matches(acceptance):
            slot = replace(slot, accepted=True)
        result.append(slot)
    return result


def first_shortfall(slots: Iterable[AgendaSlot], now: datetime) -> AgendaSlot | None:
    """Return the earliest slot with an open shortfall that has not started at ``now``."""
    ahead = [s for s in slots if s.open_shortfall and s.start >= now]
    return min(ahead, key=lambda s: s.start, default=None)
