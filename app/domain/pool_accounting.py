"""Interval accounting of a counted capacity pool (ADR 0122, design sections 2 and 4).

A commitment is one unit held over ``[start, until)``; ``until`` is the end of the work plus
the wrap-up (buffer) of the organisation. Intervals that only touch do not overlap. A pool
never sells a unit twice: a candidate is measured over its whole interval against the staffed
units minus the commitments, at the busiest moment, not per start time.

    units   2 ┤████████████████████████████████   staffed (09:00–12:00)
            1 ┤        ▒▒▒▒▒▒▒▒                    R1 at 10:00, 30 min
            0 ┤        ▒▒▒▒▒▒▒▒                    R2 at 10:00, 30 min
                09:55 10:00      10:30
      on demand at 09:55 for 20 min: free is 0 at 10:00, may not start
      earliest start: 10:30 (both reservations end)

Free units only ever drop where a commitment starts or the staffing drops, so those moments,
and the start of the interval, are the only ones that are measured.

Nothing here reads or writes a database, and nothing rewrites a commitment: when staffing falls
below the commitments, ``shortfalls`` lists the moments and ``admit_appointment`` refuses new
work over them (decision P3). The rules in one place:

* appointments are admitted by planned ends; an overrun never refuses a future appointment;
* a start without appointment, or an early start, is refused while no unit is free, and an
  occupation that runs past its planned end counts as busy (invariant 7, decision P8);
* an appointment that is due (its own commitment has begun) may start when a colleague's
  overrun holds the unit it needs now (someone is staffed, no unit is free at this moment);
  the outcome says the conflict is to be recorded. A shortage that lies later in its interval
  (a reservation that comes next) is reported, never overridden: it is that reservation's own
  start that is then due and may be recorded as a conflict.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode
from app.domain.pool_staffing import Intake, Supply, check_interval, lowest_units, require_aware
from app.domain.scheduling import MAX_SLOT_MINUTES, valid_slot_minutes
from app.domain.settings_registry import BY_KEY

MAX_BUFFER_MINUTES = int(BY_KEY["treatment_buffer_minutes"].maximum or 0)
"""Upper bound of ``treatment_buffer_minutes``: the bound of the setting in the registry."""
MAX_SPAN = timedelta(minutes=MAX_SLOT_MINUTES + MAX_BUFFER_MINUTES)
"""The longest a commitment can last; bounds how far back an adapter must look for overlaps."""

CommitmentKey = tuple[str, int]
"""``("booking", id)`` for an appointment, ``("proposal", id)`` for an open proposal of a new time
(ADR 0057), ``("request", id)`` for on-demand work."""

_Live = tuple[datetime, datetime | None]
"""``(start, until)`` of a commitment in force; ``until`` is ``None`` for an overrun."""


@dataclass(frozen=True)
class Commitment:
    """One unit of a pool held over ``[start, until)``.

    Attributes:
        key: Identity; one commitment per key counts (a booking's hold and its occupation are
            one unit, never two).
        start: First instant held (UTC).
        until: First instant free again: planned end plus buffer; after ``start``.
        in_progress: Treatment has begun; if it runs past ``until`` it is an overrun.
    """

    key: CommitmentKey
    start: datetime
    until: datetime
    in_progress: bool = False

    def __post_init__(self) -> None:
        """Require aware instants and ``start < until``.

        Raises:
            DomainError: ``invalid_configuration`` with field ``start`` or ``until``.
        """
        require_aware(self.start, "start")
        require_aware(self.until, "until")
        if self.start >= self.until:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "until")


class AdmissionOutcome(StrEnum):
    """Why an appointment is, or is not, admitted."""

    ADMITTED = "admitted"
    NOT_STAFFED = "not_staffed"
    """Some moment of the interval has no units that take appointments (a gap, a lunch, a
    holiday, an on-demand-only block)."""
    FULL = "full"
    """Staffed all through, but no unit is free at some moment of the interval."""


@dataclass(frozen=True)
class Admission:
    """The answer of ``admit_appointment``: ``free`` is the lowest free count over the interval."""

    outcome: AdmissionOutcome
    free: int

    @property
    def admitted(self) -> bool:
        """Whether the appointment may be booked."""
        return self.outcome is AdmissionOutcome.ADMITTED


class StartOutcome(StrEnum):
    """What may happen when work asks to start."""

    MAY_START = "may_start"
    OVERRUN_CONFLICT = "overrun_conflict"
    """A due appointment starts although a colleague's overrun holds the last unit now; the
    conflict is recorded (P8)."""
    REFUSED = "refused"


@dataclass(frozen=True)
class StartCheck:
    """The answer of ``check_start``.

    Attributes:
        outcome: May start, starts with a recorded conflict, or is refused.
        free: Lowest free count over the interval (negative when more work than units).
        short_at: First moment of the interval with no free unit; ``None`` when ``free >= 1``.
            Also set on ``MAY_START`` of a due appointment that is late: a unit is free now but
            the interval runs into work reserved for later (``free`` is then below 1).
    """

    outcome: StartOutcome
    free: int
    short_at: datetime | None

    @property
    def may_start(self) -> bool:
        """Whether treatment may begin now (with a conflict to record, or without)."""
        return self.outcome is not StartOutcome.REFUSED


@dataclass(frozen=True)
class Shortfall:
    """A stretch with more commitments than staffed units."""

    start: datetime
    end: datetime
    units: int
    load: int


def check_lengths(duration_minutes: int, buffer_minutes: int) -> None:
    """Validate an appointment length and a buffer.

    The length is a multiple of 5 minutes from 5 to 480 (``valid_slot_minutes``), the buffer a
    whole number of minutes from 0 to ``MAX_BUFFER_MINUTES``.

    Raises:
        DomainError: ``invalid_configuration`` with field ``duration`` or ``buffer``.
    """
    if not valid_slot_minutes(duration_minutes):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "duration")
    if not 0 <= buffer_minutes <= MAX_BUFFER_MINUTES:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "buffer")


def interval_end(start: datetime, duration_minutes: int, buffer_minutes: int) -> datetime:
    """Return ``start + duration + buffer``, the end of the interval a commitment holds.

    Example: 10:00, 30 minutes, buffer 5 holds 10:00 to 10:35.

    Raises:
        DomainError: ``invalid_configuration`` for a duration or buffer out of bounds.
    """
    check_lengths(duration_minutes, buffer_minutes)
    require_aware(start, "start")
    return start + timedelta(minutes=duration_minutes + buffer_minutes)


def one_per_key(commitments: Iterable[Commitment]) -> list[Commitment]:
    """Return the commitments with one per key; of a repeated key the last one wins.

    Pass a booking's hold first and its occupation after it: a reservation that is checked in
    or started carries one unit, not two (invariant 3).
    """
    return list({c.key: c for c in commitments}.values())


def _live(
    commitments: Iterable[Commitment],
    lower: datetime,
    upper: datetime,
    ignore: CommitmentKey | None,
    overrun_at: datetime | None,
) -> list[_Live]:
    """Return the commitments in force over ``lower`` to ``upper``.

    A started commitment past its planned end at ``overrun_at`` has no known end (``None``)
    and so lasts to the end of any interval. ``ignore`` leaves one key out.
    """
    live: list[_Live] = []
    for c in one_per_key(commitments):
        overrun = overrun_at is not None and c.in_progress and c.until <= overrun_at
        until = None if overrun else c.until
        if c.key != ignore and c.start < upper and (until is None or until > lower):
            live.append((c.start, until))
    return live


def _load_at(live: Sequence[_Live], moment: datetime) -> int:
    """Return how many of ``live`` hold a unit at ``moment`` (``start <= moment < until``)."""
    return sum(1 for start, until in live if start <= moment and (until is None or moment < until))


def _free_profile(
    supply: Supply, live: Sequence[_Live], lower: datetime, upper: datetime, intake: Intake
) -> list[tuple[datetime, int]]:
    """Return ``(moment, free units)`` at every moment of the interval where free can drop."""
    moments = {lower, *supply.borders_between(lower, upper)}
    moments |= {start for start, _ in live if lower < start < upper}
    return [(m, supply.units_at(m, intake) - _load_at(live, m)) for m in sorted(moments)]


def free_over(
    supply: Supply,
    commitments: Iterable[Commitment],
    lower: datetime,
    upper: datetime,
    intake: Intake,
    *,
    ignore: CommitmentKey | None = None,
    overrun_at: datetime | None = None,
) -> int:
    """Return the lowest number of free units at any moment of ``lower`` to ``upper``.

    Free is the units staffed for ``intake`` minus the commitments held at that moment; it is
    negative when there is more work than staff. Touching intervals do not overlap: a
    commitment that ends at ``lower``, or starts at ``upper``, does not count.

    Args:
        supply: The staffed segments.
        commitments: Everything that holds a unit (``one_per_key`` is applied).
        lower: Start of the interval (UTC).
        upper: End of the interval, after ``lower``.
        intake: Which block offers count as staffed.
        ignore: A key to leave out, for the work that asks to start (check-in reuse).
        overrun_at: When set, a started commitment past its planned end at this instant holds
            its unit to the end of the interval (an overrun is busy until released).

    Example:
        Two units, two commitments over 10:00–10:30: ``free_over`` of 09:55–10:15 is 0 (the
        moment 10:00 is full), of 10:30–10:50 is 2.

    Raises:
        DomainError: ``invalid_configuration`` with field ``interval`` if the interval is empty,
            reversed or naive.
    """
    check_interval(lower, upper)
    live = _live(commitments, lower, upper, ignore, overrun_at)
    return min(free for _, free in _free_profile(supply, live, lower, upper, intake))


def admit_appointment(
    supply: Supply,
    commitments: Iterable[Commitment],
    start: datetime,
    duration_minutes: int,
    buffer_minutes: int,
) -> Admission:
    """Decide whether an appointment may be booked at ``start``.

    The interval is ``start`` to ``start + duration + buffer``. It is not staffed when any
    moment of it has no units that take appointments; it is full when the lowest free count
    over it is below 1. The whole interval is measured, not just its start: a moment where
    other work overlaps makes the candidate full even if its first minute is free. A moment
    where staffing has dropped below the commitments is short too, so nothing new is admitted
    over it, and nothing existing is touched (decision P3).

    Example:
        Pool of 2 units, a commitment 09:00–09:30 and one 09:15–09:30: a 30-minute request at
        09:00 is ``FULL`` (free is 0 at 09:15); a 15-minute request at 09:00 is admitted with
        ``free == 1``.

    Raises:
        DomainError: ``invalid_configuration`` for a duration or buffer out of bounds.
    """
    end = interval_end(start, duration_minutes, buffer_minutes)
    if lowest_units(supply, start, end, Intake.APPOINTMENT) == 0:
        return Admission(AdmissionOutcome.NOT_STAFFED, 0)
    free = free_over(supply, commitments, start, end, Intake.APPOINTMENT)
    outcome = AdmissionOutcome.ADMITTED if free >= 1 else AdmissionOutcome.FULL
    return Admission(outcome, free)


def check_start(
    supply: Supply,
    commitments: Iterable[Commitment],
    now: datetime,
    duration_minutes: int,
    buffer_minutes: int,
    *,
    intake: Intake,
    own_key: CommitmentKey | None = None,
) -> StartCheck:
    """Decide whether work may start treatment at ``now``.

    The interval tested is ``now`` to ``now + duration + buffer``; it must fit without taking
    a unit that is reserved for someone else. A commitment in progress past its planned end
    counts as busy for the whole interval (invariant 7). The call is made again under the lock
    at the moment of the start, so a late start sees the current staffing and load (A35).

    ``own_key`` is the commitment of the work itself, left out so a checked-in reservation does
    not count twice; it belongs to an appointment, so ``intake`` must then be ``APPOINTMENT``.
    ``intake`` has no default: a due appointment in a block for appointments only is judged
    against that block's units, and a wrong default would refuse it.

    An appointment is due when its own commitment has begun and not ended
    (``start <= now < until``). The rule of decision P8, in one place:

    * enough free units over the interval: ``MAY_START``;
    * otherwise, when due and someone is staffed for appointments at ``now`` and no unit is
      free *at ``now``* (a colleague's commitments, an overrun among them, hold them all):
      ``OVERRUN_CONFLICT``, the caller records the conflict;
    * otherwise, when due and a unit is free at ``now`` but the interval runs into later work
      (a late arrival into the next reservation): ``MAY_START`` with ``short_at`` set to that
      later moment and ``free`` below 1; the next reservation is not displaced, its own start is
      the one that is due then;
    * work without a commitment, an early start, a start after the own ``until`` and a start
      where nobody is staffed (staffing dropped) are ``REFUSED``.

    Example:
        Two units, reservations R1 and R2 at 10:00 for 30 minutes: an on-demand request of 20
        minutes at 09:55 is refused with ``short_at == 10:00``; R1 itself starting at 10:00
        may start with ``free == 1``.

    Raises:
        DomainError: ``invalid_configuration`` for a duration or buffer out of bounds, or for
            ``own_key`` with ``intake`` ``ON_DEMAND`` (field ``own_key``).
    """
    end = interval_end(now, duration_minutes, buffer_minutes)
    if own_key is not None and intake is not Intake.APPOINTMENT:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "own_key")
    everything = one_per_key(commitments)
    live = _live(everything, now, end, own_key, now)
    profile = _free_profile(supply, live, now, end, intake)
    free = min(value for _, value in profile)
    if free >= 1:
        return StartCheck(StartOutcome.MAY_START, free, None)
    short_at = next(moment for moment, value in profile if value < 1)
    own = next((c for c in everything if c.key == own_key), None)
    if own is None or not own.start <= now < own.until or supply.units_at(now, intake) < 1:
        return StartCheck(StartOutcome.REFUSED, free, short_at)
    free_now = profile[0][1]  # the profile starts at ``now``
    outcome = StartOutcome.MAY_START if free_now >= 1 else StartOutcome.OVERRUN_CONFLICT
    return StartCheck(outcome, free, short_at)


def earliest_start(
    supply: Supply,
    commitments: Iterable[Commitment],
    after: datetime,
    duration_minutes: int,
    buffer_minutes: int,
    limit: datetime,
    *,
    intake: Intake = Intake.ON_DEMAND,
) -> datetime | None:
    """Return the first start from ``after`` up to ``limit`` that fits, by the planned ends.

    This is a forecast, not a promise: it assumes every commitment ends at its planned end. A
    commitment in progress that has not reached its planned end at ``after`` is taken to end
    there; if it then runs over, ``check_start`` at that moment is refused. Example: work in
    progress planned to end at 10:30, asked at 10:10: the answer is 10:30, and at 10:30 an
    overrun makes ``check_start`` refuse. Call ``check_start`` again, under the lock, at the
    moment of the start (A35).

    The answer can only change where a commitment ends or a staffed segment begins, so those
    moments, and ``after`` itself, are the only candidates. A commitment in progress past its
    planned end at ``after`` has no known end and blocks every candidate. Returns ``None`` when
    nothing fits by ``limit`` (a ticket may still be issued; the queue shows no expected start).

    Example:
        The request of 20 minutes at 09:55 above: 09:55 fails (10:00 is full), 10:30 is free,
        so the answer is 10:30.

    Raises:
        DomainError: ``invalid_configuration`` for a duration or buffer out of bounds.
    """
    check_lengths(duration_minutes, buffer_minutes)
    require_aware(after, "after")
    everything = one_per_key(commitments)
    candidates = {after}
    candidates |= {c.until for c in everything if after < c.until <= limit}
    candidates |= {s.start for s in supply.segments if after < s.start <= limit}
    length = timedelta(minutes=duration_minutes + buffer_minutes)
    for candidate in sorted(candidates):
        free = free_over(
            supply, everything, candidate, candidate + length, intake, overrun_at=after
        )
        if free >= 1:
            return candidate
    return None


def shortfalls(
    supply: Supply,
    commitments: Iterable[Commitment],
    lower: datetime,
    upper: datetime,
    *,
    overrun_at: datetime | None = None,
) -> list[Shortfall]:
    """Return the stretches of ``lower`` to ``upper`` with more commitments than staffed units.

    Units count the people staffed whatever the block offers. Neighbouring stretches with the
    same units and load are one entry. Nothing is rewritten or dropped (decision P3): the list
    is what a staffing edit shows and what the planner works through. With ``overrun_at`` a
    started commitment past its planned end stays in the load to ``upper`` (invariant 7).

    Example:
        Two units, three commitments over 10:00–10:30: one entry, 10:00–10:30, units 2, load 3.

    Raises:
        DomainError: ``invalid_configuration`` with field ``interval`` if the interval is empty,
            reversed or naive.
    """
    check_interval(lower, upper)
    live = _live(commitments, lower, upper, None, overrun_at)
    edges = {lower, upper, *supply.borders_between(lower, upper)}
    for start, until in live:
        edges |= {e for e in (start, until) if e is not None and lower < e < upper}
    ordered = sorted(edges)
    found: list[Shortfall] = []
    for start, end in zip(ordered, ordered[1:], strict=False):
        units, load = supply.units_at(start), _load_at(live, start)
        if load <= units:
            continue
        previous = found[-1] if found else None
        if previous and previous.end == start and (previous.units, previous.load) == (units, load):
            found[-1] = Shortfall(previous.start, end, units, load)
        else:
            found.append(Shortfall(start, end, units, load))
    return found
