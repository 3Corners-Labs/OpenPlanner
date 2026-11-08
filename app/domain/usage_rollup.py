"""How usage facts become hourly cells (ADR 0117 point 4, step 4; pure functions).

The worker reads the facts after its watermark, turns each into the cells of the metrics it
feeds, adds cells of the same hour and cut together, and stores the sums. The same facts always
give the same cells, in any order, so a rebuild from the outbox gives the table it had:

    UsageFact ─► ``cells_of`` (one cell per metric it feeds, with the cut that metric reads)
              ─► ``merge`` (same hour, group, metric and cut: count and sum add, maximum stays)
              ─► ``UsageCell`` rows for ``usage_hour``

A fact never feeds a metric whose catalogue entry does not name its kind (a test checks that).
The kinds that have no producer yet are in ``UNMAPPED``. They make no cell, and the watermark
passes them: a kind starts to count from the release that maps it, and a rebuild from the outbox
covers only what was mapped when it ran. A team member's fact is passed over the same way until
step 7 maps it.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from app.domain.usage import (
    METRICS,
    MOVE_REASON,
    FactKind,
    StaffFact,
    UsageCell,
    UsageFact,
    UsageOutcome,
    duration_bucket,
)


@dataclass(frozen=True)
class _Feed:
    """One metric a fact kind feeds, and what its cell reads of the fact.

    Attributes:
        metric: The metric id of ``usage.METRICS``.
        duration: Whether the fact's seconds are the figure and fall in a bucket.
        signed: Whether that duration uses the mirrored buckets (it may be negative).
        cuts: The fact fields the cell keeps as its cut columns.
        fixed: Cut columns the metric sets to a fixed word (which way a closure change went).
        only: A condition on the fact; the cell is made only when it holds.
    """

    metric: str
    duration: bool = False
    signed: bool = False
    cuts: tuple[str, ...] = ()
    fixed: tuple[tuple[str, str], ...] = ()
    only: Callable[[UsageFact], bool] | None = None


def _no_move(fact: UsageFact) -> bool:
    """A booking made by a move is no new booking: it feeds no volume metric."""
    return fact.reason_code != MOVE_REASON


def _by_participant(fact: UsageFact) -> bool:
    """Only a participant's cancellation feeds the rate and the lateness."""
    return fact.outcome is UsageOutcome.CANCELLED_BY_PARTICIPANT


_FEEDS: dict[FactKind, tuple[_Feed, ...]] = {
    FactKind.BOOKING_CREATED: (
        _Feed("U1", only=_no_move),
        _Feed("U6", duration=True, only=_no_move),
        _Feed("Q1", cuts=("booking_kind", "dispatch_kind"), only=_no_move),
        _Feed("M2", cuts=("locale",), only=_no_move),
    ),
    FactKind.BOOKING_SLOTTED: (_Feed("C4"),),
    FactKind.BOOKING_CONFIRMED: (_Feed("U9", duration=True),),
    FactKind.BOOKING_CONFIRM_EXPIRED: (
        _Feed("U2", cuts=("outcome",)),
        _Feed("U9", cuts=("outcome",)),
    ),
    FactKind.BOOKING_CANCELLED: (
        _Feed("U2", cuts=("outcome",)),
        _Feed("U4", cuts=("actor_class",), only=_by_participant),
        _Feed("U5", duration=True, signed=True, only=_by_participant),
    ),
    FactKind.BOOKING_MOVED: (_Feed("U7", cuts=("actor_class", "reason_code")),),
    FactKind.BOOKING_SUSPENDED: (
        _Feed("U10", cuts=("actor_class",), fixed=(("reason_code", "suspended"),)),
    ),
    FactKind.BOOKING_RESUMED: (
        _Feed("U10", cuts=("actor_class",), fixed=(("reason_code", "resumed"),)),
    ),
}
"""The metrics each fact kind feeds. A kind that is not here has no producer yet."""


def _check_against_the_catalogue() -> None:
    """Refuse, at import, a feed that names a metric which does not list its fact kind."""
    facts_of_metric = {m.id: set(m.facts) for m in METRICS}
    for kind, feeds in _FEEDS.items():
        for feed in feeds:
            if kind not in facts_of_metric.get(feed.metric, set()):
                raise ValueError(f"{feed.metric} does not list {kind.value} in the catalogue")


_check_against_the_catalogue()


def _cell(fact: UsageFact, feed: _Feed) -> UsageCell:
    """Return the cell of one fact for one metric: a count of one and its figure."""
    seconds = fact.value_seconds if feed.duration else None
    value = seconds or 0
    base = UsageCell(
        fact.bucket_hour,
        fact.organisation_id,
        fact.group_id,
        feed.metric,
        1,
        value,
        value,
        bucket=duration_bucket(seconds, signed=feed.signed) if seconds is not None else None,
    )
    cuts: dict[str, Any] = {name: getattr(fact, name) for name in feed.cuts}
    cuts.update(feed.fixed)
    return replace(base, **cuts)


UNMAPPED: frozenset[FactKind] = frozenset(set(FactKind) - set(_FEEDS))
"""The kinds no step writes yet (calls, queue, rebooking, slots, messages, trial calls): they
make no cell until the step that writes them adds their rule."""


def cells_of(fact: UsageFact) -> list[UsageCell]:
    """Return the cells ``fact`` feeds, one per metric; none for a kind that is not mapped."""
    return [
        _cell(fact, feed)
        for feed in _FEEDS.get(fact.kind, ())
        if feed.only is None or feed.only(fact)
    ]


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
    """Return the merged cells of ``facts``; a team member's fact makes none until step 7."""
    return merge(c for f in facts if isinstance(f, UsageFact) for c in cells_of(f))


BATCH_SIZE = 500
"""Facts rolled up per transaction: a fixed bound on the write lock, like
``booking_rollup.BATCH_SIZE``, not a policy."""

MAX_BATCHES = 20
"""Batches one run rolls up before it yields to the rest of the worker loop; what is left is
picked up by the next pass, and System status shows the facts still waiting."""

PRUNE_BATCH = 1000
"""Rows one transaction deletes when retention prunes: a fixed bound on the write lock."""

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
