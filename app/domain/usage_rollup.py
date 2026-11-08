"""How usage facts become hourly cells (ADR 0117 point 4, step 4; pure functions).

The worker reads the facts after its watermark, turns each into the cells of the metrics it
feeds, adds cells of the same hour and cut together, and stores the sums. The same facts always
give the same cells, in any order, so a rebuild from the outbox gives the table it had:

    UsageFact ─► ``cells_of`` (one cell per metric it feeds, with the cut that metric reads)
              ─► ``merge`` (same hour, group, metric and cut: count and sum add, maximum stays)
              ─► ``UsageCell`` rows for ``usage_hour``

A fact never feeds a metric whose catalogue entry does not name its kind (a test checks that).
The kinds that have no producer yet are in ``UNMAPPED``: they make no cell until the step that
writes them maps them, so this module and the catalogue stay in step.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.domain.usage import (
    MOVE_REASON,
    ActorClass,
    FactKind,
    StaffFact,
    UsageCell,
    UsageFact,
    UsageOutcome,
    duration_bucket,
)

_Rule = Callable[[UsageFact], list[UsageCell]]


def _cell(
    fact: UsageFact,
    metric: str,
    *,
    duration: bool = False,
    signed: bool = False,
    **cuts: object,
) -> UsageCell:
    """Return the cell of one fact for ``metric``: a count of one and its figure.

    With ``duration`` the fact's seconds are the figure and fall in their bucket; ``signed``
    uses the mirrored buckets. Anything in ``cuts`` is the cut the metric reads.
    """
    seconds = fact.value_seconds if duration else None
    value = seconds or 0
    return UsageCell(
        fact.bucket_hour,
        fact.organisation_id,
        fact.group_id,
        metric,
        1,
        value,
        value,
        bucket=duration_bucket(seconds, signed=signed) if seconds is not None else None,
        **cuts,  # type: ignore[arg-type]
    )


def _created(fact: UsageFact) -> list[UsageCell]:
    """Volume, lead time, queue method and locale; a booking made by a move is none of them."""
    if fact.reason_code == MOVE_REASON:
        return []
    return [
        _cell(fact, "U1"),
        _cell(fact, "U6", duration=True),
        _cell(fact, "Q1", booking_kind=fact.booking_kind, dispatch_kind=fact.dispatch_kind),
        _cell(fact, "M2", locale=fact.locale),
    ]


def _confirmed(fact: UsageFact) -> list[UsageCell]:
    """The conversion of unconfirmed bookings, with the time it took."""
    return [_cell(fact, "U9", duration=True)]


def _confirm_expired(fact: UsageFact) -> list[UsageCell]:
    """An outcome, and the other half of the conversion."""
    return [_cell(fact, "U2", outcome=fact.outcome), _cell(fact, "U9", outcome=fact.outcome)]


def _cancelled(fact: UsageFact) -> list[UsageCell]:
    """An outcome; a participant's cancellation also feeds the rate and the lateness."""
    cells = [_cell(fact, "U2", outcome=fact.outcome)]
    if fact.outcome is UsageOutcome.CANCELLED_BY_PARTICIPANT:
        cells.append(_cell(fact, "U4", actor_class=fact.actor_class))
        cells.append(_cell(fact, "U5", duration=True, signed=True))
    return cells


def _moved(fact: UsageFact) -> list[UsageCell]:
    """A reschedule, by who and for which reason."""
    return [_cell(fact, "U7", actor_class=fact.actor_class, reason_code=fact.reason_code)]


def _closure(word: str) -> _Rule:
    """Return the rule of a closure change: the reason cut says which way it went."""

    def rule(fact: UsageFact) -> list[UsageCell]:
        return [_cell(fact, "U10", actor_class=ActorClass.CLOSURE, reason_code=word)]

    return rule


_RULES: dict[FactKind, _Rule] = {
    FactKind.BOOKING_CREATED: _created,
    FactKind.BOOKING_SLOTTED: lambda fact: [_cell(fact, "C4")],
    FactKind.BOOKING_CONFIRMED: _confirmed,
    FactKind.BOOKING_CONFIRM_EXPIRED: _confirm_expired,
    FactKind.BOOKING_CANCELLED: _cancelled,
    FactKind.BOOKING_MOVED: _moved,
    FactKind.BOOKING_SUSPENDED: _closure("suspended"),
    FactKind.BOOKING_RESUMED: _closure("resumed"),
}

UNMAPPED: frozenset[FactKind] = frozenset(set(FactKind) - set(_RULES))
"""The kinds no step writes yet (calls, queue, rebooking, slots, messages, trial calls): they
make no cell until the step that writes them adds their rule."""


def cells_of(fact: UsageFact) -> list[UsageCell]:
    """Return the cells ``fact`` feeds, one per metric; none for a kind that is not mapped."""
    rule = _RULES.get(fact.kind)
    return rule(fact) if rule is not None else []


def _key(cell: UsageCell) -> UsageCell:
    """Return ``cell`` with its figures zeroed: what makes two cells the same row."""
    return replace(cell, count=0, sum_value=0, max_value=0)


def merge(cells: Iterable[UsageCell]) -> list[UsageCell]:
    """Add cells of the same hour, group, metric and cut together.

    Counts and sums add; the maximum stays the largest. The result is in no particular order.
    """
    merged: dict[UsageCell, UsageCell] = {}
    for cell in cells:
        key = _key(cell)
        found = merged.get(key)
        merged[key] = (
            cell
            if found is None
            else replace(
                found,
                count=found.count + cell.count,
                sum_value=found.sum_value + cell.sum_value,
                max_value=max(found.max_value, cell.max_value),
            )
        )
    return list(merged.values())


def roll(facts: Sequence[UsageFact | StaffFact]) -> list[UsageCell]:
    """Return the merged cells of ``facts``; a team member's fact waits for step 7."""
    return merge(c for f in facts if isinstance(f, UsageFact) for c in cells_of(f))


BATCH_SIZE = 500
"""Facts rolled up per transaction: a fixed bound on the write lock, like
``booking_rollup.BATCH_SIZE``, not a policy."""

LAG_FACTOR = 4
"""System status warns when the rollup is this many intervals behind (ADR 0117 point 4)."""

AVERAGE_MONTH = timedelta(days=30, hours=10)
"""A month for the retention of ``usage_hour``: 30.4 days, a fixed convention."""


@dataclass(frozen=True)
class UsageBatch:
    """The facts after the watermark, as far as one transaction reads.

    Attributes:
        facts: The readable facts, oldest first.
        last_id: The id of the last row read, readable or not; the watermark moves to it.
        unreadable: Rows whose values this version cannot read (a newer version wrote them);
            they are passed over and counted, never allowed to stop the rollup.
    """

    facts: tuple[UsageFact | StaffFact, ...]
    last_id: int
    unreadable: int = 0


@dataclass(frozen=True)
class UsageState:
    """Where the rollup stands.

    Attributes:
        last_event_id: The watermark: the last fact rolled into ``usage_hour``.
        rolled_at: When the rollup last finished a run; ``None`` if it never has.
        pending: Facts written after the watermark.
        backfilled_at: When the backfill of old bookings was completed; ``None`` before.
    """

    last_event_id: int
    rolled_at: datetime | None
    pending: int
    backfilled_at: datetime | None = None


@dataclass(frozen=True)
class UsagePruned:
    """What retention removed in one run: counts only."""

    events: int = 0
    hours: int = 0


def is_lagging(state: UsageState, now: datetime, interval: timedelta) -> bool:
    """Return whether System status should warn: facts wait and the last run is long ago.

    A rollup that never ran with facts waiting counts as lagging; the worker's first loop
    runs it, so that only shows between an update and the worker coming up.
    """
    if state.pending == 0:
        return False
    return state.rolled_at is None or now - state.rolled_at > LAG_FACTOR * interval


def hours_cutoff(now: datetime, months: int) -> datetime:
    """Return the hour before which ``usage_hour`` rows are deleted."""
    return now - months * AVERAGE_MONTH


def events_cutoff(now: datetime, days: int) -> datetime:
    """Return the hour before which rolled-up facts are deleted."""
    return now - timedelta(days=days)
