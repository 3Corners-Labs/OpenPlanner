"""How usage facts become hourly cells (ADR 0117 point 4, step 4; pure functions).

The worker reads the facts after its watermark, turns each into the cells of the metrics it
feeds, adds cells of the same hour and cut together, and stores the sums. The same facts always
give the same cells, in any order, so a rebuild from the outbox gives the table it had:

    UsageFact ─► ``cells_of`` (one cell per metric it feeds, with the cut that metric reads)
              ─► ``merge`` (same hour, group, metric and cut: count and sum add, maximum stays)
              ─► ``UsageCell`` rows for ``usage_hour``

A fact never feeds a metric whose catalogue entry does not name its kind (a test checks that),
and every kind has a feed (a test checks that too). A team member's fact makes no cell here: it
feeds ``usage_staff_hour`` through ``app.domain.usage_staff``. A kind that is mapped in a later
release counts from that release: the watermark has passed the earlier facts, and a rebuild
from the outbox covers only what was mapped when it ran.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from app.domain.booking import BookingKind
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
        tag: A function of the fact that sets the ``reason_code`` cut: the metric's own state
            word, such as a fill class or ``confirmation.sent``. It replaces a ``reason_code``
            taken from the fact.
        figure: A function of the fact that gives the cell's figure instead of the seconds: a
            number of people, a capacity, a load. The count of the cell stays one per fact.
        only: A condition on the fact; the cell is made only when it holds.

    A duration of a call whose start was never seen is missing, not short: those feeds use
    ``_timed`` so the mean is over the calls that have one.
    """

    metric: str
    duration: bool = False
    signed: bool = False
    cuts: tuple[str, ...] = ()
    fixed: tuple[tuple[str, str], ...] = ()
    tag: Callable[[UsageFact], str] | None = None
    figure: Callable[[UsageFact], int] | None = None
    only: Callable[[UsageFact], bool] | None = None


def _no_move(fact: UsageFact) -> bool:
    """A booking made by a move is no new booking: it feeds no volume metric."""
    return fact.reason_code != MOVE_REASON


def _by_participant(fact: UsageFact) -> bool:
    """Only a participant's cancellation feeds the rate and the lateness."""
    return fact.outcome is UsageOutcome.CANCELLED_BY_PARTICIPANT


def _timed(fact: UsageFact) -> bool:
    """The fact has its seconds: a call whose start or end was never seen has none."""
    return fact.value_seconds is not None


def _scheduled(fact: UsageFact) -> bool:
    """Only a scheduled call has a start to be punctual or late against (metric Q3)."""
    return fact.booking_kind is BookingKind.SCHEDULED and _timed(fact)


def _direct(fact: UsageFact) -> bool:
    """Only a direct call counts for the waiting and the load (metrics Q6 and Q9)."""
    return fact.booking_kind is BookingKind.DIRECT


def _direct_timed(fact: UsageFact) -> bool:
    """A direct call that was left, with the wait at that moment (metric Q6)."""
    return _direct(fact) and _timed(fact)


def _direct_load(fact: UsageFact) -> bool:
    """A direct call's creation that says how many calls were open (metric Q9)."""
    return _direct(fact) and fact.participants is not None


def _people(fact: UsageFact) -> int:
    """The most people seen in a finished call (metric U11)."""
    return fact.participants or 0


def _load(fact: UsageFact) -> int:
    """The direct calls waiting or connected right after a call was created (metric Q9)."""
    return fact.participants or 0


def _booked(fact: UsageFact) -> int:
    """The bookings of a closed slot that count (metric C1)."""
    return fact.booked or 0


def _capacity(fact: UsageFact) -> int:
    """The capacity snapshot of a closed slot (metric C1)."""
    return fact.capacity or 0


def _idle(fact: UsageFact) -> int:
    """The places of a closed slot nobody booked (metric C3)."""
    return max(_capacity(fact) - _booked(fact), 0)


FILL_CLASSES: tuple[str, ...] = ("empty", "under_half", "under_full", "full")
"""The fill classes of metric C2, in order."""


def fill_class(fact: UsageFact) -> str:
    """Return how full a closed slot was: empty, under half, under full or full (metric C2)."""
    booked, capacity = _booked(fact), _capacity(fact)
    if booked <= 0:
        return FILL_CLASSES[0]
    if booked >= capacity:
        return FILL_CLASSES[3]
    return FILL_CLASSES[1] if booked * 2 < capacity else FILL_CLASSES[2]


def _message_state(state: str) -> Callable[[UsageFact], str]:
    """Return the tag of a message fact: its purpose and whether it was sent or failed (M1)."""
    return lambda fact: f"{fact.reason_code}.{state}"


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
    FactKind.BOOKING_OUTCOME: (
        _Feed("U2", cuts=("outcome",)),
        _Feed("U3", cuts=("outcome",)),
    ),
    FactKind.BOOKING_SUSPENDED: (
        _Feed("U10", cuts=("actor_class",), fixed=(("reason_code", "suspended"),)),
    ),
    FactKind.BOOKING_RESUMED: (
        _Feed("U10", cuts=("actor_class",), fixed=(("reason_code", "resumed"),)),
    ),
    FactKind.REBOOK_PROPOSED: (
        _Feed("U8", cuts=("actor_class",), fixed=(("reason_code", "proposed"),)),
    ),
    FactKind.REBOOK_ACCEPTED: (_Feed("U8", duration=True, fixed=(("reason_code", "accepted"),)),),
    FactKind.REBOOK_EXPIRED: (_Feed("U8", fixed=(("reason_code", "expired"),)),),
    FactKind.CALL_CREATED: (
        _Feed("Q6", cuts=("dispatch_kind",), fixed=(("reason_code", "created"),), only=_direct),
        _Feed("Q9", figure=_load, only=_direct_load),
    ),
    FactKind.CALL_CONNECTED: (
        _Feed("Q2", duration=True, cuts=("booking_kind", "dispatch_kind"), only=_timed),
    ),
    FactKind.CALL_HOST_JOINED: (
        _Feed("Q3", duration=True, signed=True, cuts=("dispatch_kind",), only=_scheduled),
    ),
    FactKind.CALL_GUEST_WAITED: (_Feed("Q4", duration=True, cuts=("dispatch_kind",), only=_timed),),
    FactKind.CALL_ENDED: (
        _Feed("Q5", duration=True, cuts=("booking_kind", "dispatch_kind"), only=_timed),
        _Feed("U11", figure=_people, cuts=("booking_kind",)),
    ),
    FactKind.CALL_WAITING_LEFT: (
        _Feed("Q6", duration=True, cuts=("dispatch_kind", "reason_code"), only=_direct_timed),
    ),
    FactKind.CALL_NOT_PICKED_UP: (
        _Feed(
            "Q6", cuts=("booking_kind", "dispatch_kind"), fixed=(("reason_code", "not_picked_up"),)
        ),
    ),
    FactKind.CALL_FAILED: (_Feed("Q8", cuts=("reason_code",)),),
    FactKind.QUEUE_CLAIMED: (
        _Feed(
            "Q7",
            duration=True,
            cuts=("dispatch_kind",),
            fixed=(("reason_code", "claimed"),),
            only=_timed,
        ),
    ),
    FactKind.QUEUE_RELEASED: (
        _Feed("Q7", cuts=("dispatch_kind",), fixed=(("reason_code", "released"),)),
    ),
    FactKind.SLOT_CLOSED: (
        _Feed("C1", figure=_booked, fixed=(("reason_code", "booked"),)),
        _Feed("C1", figure=_capacity, fixed=(("reason_code", "capacity"),)),
        _Feed("C2", tag=fill_class),
        _Feed("C3", figure=_idle),
    ),
    FactKind.SLOT_SHORTFALL: (_Feed("C5", cuts=("reason_code",)),),
    FactKind.MESSAGE_SENT: (_Feed("M1", cuts=("channel_kind",), tag=_message_state("sent")),),
    FactKind.MESSAGE_FAILED: (_Feed("M1", cuts=("channel_kind",), tag=_message_state("failed")),),
    FactKind.TRIAL_CREATED: (_Feed("T1", fixed=(("reason_code", "created"),)),),
    FactKind.TRIAL_JOINED: (_Feed("T1", fixed=(("reason_code", "joined"),)),),
    FactKind.TRIAL_ENTERED: (_Feed("T1", fixed=(("reason_code", "entered"),)),),
    FactKind.TRIAL_FAILED: (_Feed("T1", fixed=(("reason_code", "failed"),)),),
}
"""The metrics each fact kind feeds. A kind that is not here makes no cell."""


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
    value = feed.figure(fact) if feed.figure is not None else (seconds or 0)
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
    if feed.tag is not None:
        cuts["reason_code"] = feed.tag(fact)
    return replace(base, **cuts)


@dataclass(frozen=True)
class FeedRow:
    """One line of the published description of the feeds (``docs/reporting.md``).

    Attributes:
        kind: The fact kind.
        metric: The metric the cell belongs to.
        figure: What the cell's figure is: the seconds in buckets, signed seconds, a number
            the fact carries, or just the count.
        cuts: The cut columns the cell keeps, and the fixed words it sets as ``reason_code``.
        condition: When the cell is made, if not always.
    """

    kind: FactKind
    metric: str
    figure: str
    cuts: str
    condition: str


def feed_rows() -> list[FeedRow]:
    """Return what every fact kind feeds, for ``docs/reporting.md``: one row per feed."""
    rows = []
    for kind in FactKind:
        for feed in _FEEDS.get(kind, ()):
            if feed.figure is not None:
                figure = (feed.figure.__doc__ or "a number of the fact").splitlines()[0].rstrip(".")
            elif feed.duration:
                figure = "signed seconds in buckets" if feed.signed else "seconds in buckets"
            else:
                figure = "count"
            words = [f"{name} = `{word}`" for name, word in feed.fixed]
            if feed.tag is not None:
                words.append(
                    "reason_code = " + (feed.tag.__doc__ or "a state word").splitlines()[0]
                )
            cuts = ", ".join([*(f"`{c}`" for c in feed.cuts), *words]) or "none"
            condition = (feed.only.__doc__ or "").splitlines()[0].rstrip(".") if feed.only else ""
            rows.append(FeedRow(kind, feed.metric, figure, cuts, condition))
    return rows


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
    """Return the merged cells of ``facts``; a team member's fact makes none here."""
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
