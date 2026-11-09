"""When the rebooking assistant sends its proposals by itself (ADR 0057 point 6).

A group may let the assistant propose new times without a planner's review. A staffing change
that leaves a slot short must not act at once, so a typo can be undone first; and staffing has
many sources (blocks, exceptions, holidays, closures, staff), so no version number is kept.
Instead a scan looks at the short slots now and remembers, per slot, the *fingerprint* of its
shortfall (agents and bookings) and since when it has been the same. A shortfall that stayed the
same for the group's delay is *due*; one that changed starts waiting again; one that is gone is
forgotten. This module holds those rules; ``PlannerService.run_automatic`` applies them.

    short slots now ──► scan ──► store (new or changed)    wait, from this moment
                              ├─► gone                      forget
                              └─► due                       the same for the whole delay
    due bookings ≤ ceiling ──► propose (system as actor)
    due bookings > ceiling ──► send nothing, tell the planners once
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta


@dataclass(frozen=True)
class ShortfallWatch:
    """Since when a slot has been short at the same counts.

    Attributes:
        group_id: The slot's group.
        slot_start: The slot's start (UTC).
        fingerprint: The counts the shortfall was seen at (``fingerprint_of``).
        first_seen_at: When it was first seen at exactly these counts.
        notified_at: When the planners were told that a review waits (too many bookings), once
            per fingerprint; ``None`` until then.
    """

    group_id: int
    slot_start: datetime
    fingerprint: str
    first_seen_at: datetime
    notified_at: datetime | None = None

    def is_due(self, now: datetime, delay: timedelta) -> bool:
        """Whether the shortfall has stayed the same for the whole ``delay``."""
        return now - self.first_seen_at >= delay

    def notify(self, now: datetime) -> "ShortfallWatch":
        """Return the watch with the planners told at ``now``."""
        return replace(self, notified_at=now)


@dataclass(frozen=True)
class Seen:
    """A slot that is short right now, at the counts the scan read."""

    slot_start: datetime
    fingerprint: str


@dataclass(frozen=True)
class Scan:
    """What one scan decides for a group.

    Attributes:
        store: Watches to write: shortfalls seen for the first time or at other counts.
        gone: Starts of watches whose slot is no longer short.
        due: Watches whose shortfall has stayed the same for the whole delay.
    """

    store: tuple[ShortfallWatch, ...]
    gone: tuple[datetime, ...]
    due: tuple[ShortfallWatch, ...]


REVIEW_WAITING_EVENT = "planner.auto_review_waiting"
"""The system event that tells the planners a review waits because too many bookings are
affected for automatic sending (point 6)."""


AUTO_PROPOSAL_EVENT = "planner.auto_proposal"
"""The system event of one booking the assistant asked to move or rebook by itself. Not a
``bookings.manage`` change: no planner made it, so it never counts towards the hourly alert."""


def fingerprint_of(agents: int, booked: int) -> str:
    """Return the fingerprint of a shortfall: the agents on duty and the places taken."""
    return f"{agents}/{booked}"


def scan(
    group_id: int,
    seen: Sequence[Seen],
    known: Mapping[datetime, ShortfallWatch],
    now: datetime,
    delay: timedelta,
) -> Scan:
    """Compare the short slots ``seen`` now with the watches ``known`` from earlier scans."""
    store: list[ShortfallWatch] = []
    due: list[ShortfallWatch] = []
    for slot in seen:
        watch = known.get(slot.slot_start)
        if watch is None or watch.fingerprint != slot.fingerprint:
            store.append(ShortfallWatch(group_id, slot.slot_start, slot.fingerprint, now))
        elif watch.is_due(now, delay):
            due.append(watch)
    present = {slot.slot_start for slot in seen}
    gone = tuple(start for start in known if start not in present)
    return Scan(tuple(store), gone, tuple(due))


def needs_review(bookings: int, ceiling: int) -> bool:
    """Whether so many bookings are affected that a planner must review instead (point 6)."""
    return bookings > ceiling
