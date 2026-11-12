"""Which starts of a flow on a counted pool are free, computed once per scene (ADR 0122 step 5).

Pure functions and values, no port. A *scene* is what availability needs from one pool over one
stretch of time, loaded once: the flow's grid starts (already narrowed to the booking window and
outside closures), the staffed segments and the commitments. ``PoolScene.offered`` then admits
each start against only what overlaps it:

    commitments, sorted by start once     segments (sorted, never overlapping)
          │ bisect (start - MAX_SPAN, until)     │ bisect (end > start, start < until)
          ▼                                      ▼
    admit_appointment(clipped supply, overlapping commitments, start, length, wrap-up)

A commitment lasts at most ``MAX_SPAN``, so one that starts ``MAX_SPAN`` or more before a
candidate cannot overlap it. The work per start is a logarithm plus what overlaps it, not every
commitment and segment of the stretch; a month of five-minute starts on a busy pool stays far
below a second (``tests/domain/test_pool_availability.py``).

Subjects do not narrow the times of a pool (decision P7): a pool's blocks carry no subjects, and
a subject chosen on the page is ignored for a flow on a pool.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.availability import AvailableSlot
from app.domain.booking import Slot
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import ResolvedFlow
from app.domain.pool_accounting import (
    MAX_SPAN,
    Commitment,
    admit_appointment,
    interval_end,
)
from app.domain.pool_staffing import Intake, Supply, lowest_units


@dataclass(frozen=True)
class PoolTarget:
    """What an appointment on a pool draws on: the pool and the appointment's length."""

    pool_id: int
    duration_minutes: int


def resolved_target(resolved: ResolvedFlow | None) -> PoolTarget | None:
    """Return what a new booking under ``resolved`` draws on; ``None`` for a legacy flow or none.

    A published revision that is not legacy always has a pool and a length (the readiness
    checklist and ``check_flow_references``); one without either is refused rather than placed on
    the service's own staffing, which would sell a unit twice.

    Raises:
        DomainError: ``slot_unavailable`` for a revision that is not legacy but lacks its pool or
            its length.
    """
    if resolved is None or resolved.legacy:
        return None
    if resolved.capacity_pool_id is None or resolved.duration_minutes is None:
        raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
    return PoolTarget(resolved.capacity_pool_id, resolved.duration_minutes)


def slot_target(slot: Slot) -> PoolTarget | None:
    """Return what the bookings of a stored ``slot`` draw on; ``None`` for a legacy slot.

    The slot is the source of truth for a booking that exists: its pool and its length were
    fixed when it was created, whatever the flow's revisions say since (A23, A36).
    """
    if slot.capacity_pool_id is None:
        return None
    minutes = (slot.end - slot.start) // timedelta(minutes=1)
    return PoolTarget(slot.capacity_pool_id, minutes)


class PoolScene:
    """The grid starts, supply and commitments of one pool flow over a stretch, loaded once.

    Args:
        target: The pool and the appointment length.
        buffer_minutes: The organisation's wrap-up, fixed for this scene.
        starts: The grid starts to judge, inside the booking window and outside closures.
        supply: The staffed segments covering every start's interval.
        commitments: The units held over the stretch (``get_pool_commitments``).
    """

    def __init__(
        self,
        target: PoolTarget,
        buffer_minutes: int,
        starts: Sequence[datetime],
        supply: Supply,
        commitments: Sequence[Commitment],
    ) -> None:
        """Index the starts, the segments and the commitments once."""
        self.target = target
        self.buffer_minutes = buffer_minutes
        self.starts = sorted(starts)
        self._segments = supply.segments
        self._segment_starts = [s.start for s in self._segments]
        self._segment_ends = [s.end for s in self._segments]
        self._load = sorted(commitments, key=lambda c: c.start)
        self._load_starts = [c.start for c in self._load]

    def _supply_over(self, lower: datetime, upper: datetime) -> Supply:
        """Return the segments that overlap ``lower`` to ``upper``."""
        first = bisect_right(self._segment_ends, lower)
        last = bisect_left(self._segment_starts, upper)
        return Supply(self._segments[first:last])

    def _load_over(self, lower: datetime, upper: datetime) -> list[Commitment]:
        """Return the commitments that overlap ``lower`` to ``upper`` (touching does not)."""
        first = bisect_right(self._load_starts, lower - MAX_SPAN)
        last = bisect_left(self._load_starts, upper)
        return [c for c in self._load[first:last] if c.until > lower]

    def offered(self, lower: datetime, upper: datetime) -> list[AvailableSlot]:
        """Return the starts in ``lower <= start < upper`` that admission lets through.

        ``capacity`` is the fewest units staffed for appointments over the slot's interval,
        ``remaining`` the fewest free units (``admit_appointment``); the slot ends after the
        appointment's length, without the wrap-up.
        """
        duration = self.target.duration_minutes
        length = timedelta(minutes=duration)
        result: list[AvailableSlot] = []
        first, last = bisect_left(self.starts, lower), bisect_left(self.starts, upper)
        for start in self.starts[first:last]:
            until = interval_end(start, duration, self.buffer_minutes)
            supply = self._supply_over(start, until)
            load = self._load_over(start, until)
            admission = admit_appointment(supply, load, start, duration, self.buffer_minutes)
            if admission.admitted:
                units = lowest_units(supply, start, until, Intake.APPOINTMENT)
                result.append(AvailableSlot(start, start + length, units, admission.free))
        return result
