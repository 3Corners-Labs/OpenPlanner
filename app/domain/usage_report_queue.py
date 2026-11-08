"""The *Queue and calls* report: how bookings reach staff and how the calls go (ADR 0117 step 6).

A pure function from the hourly cells of the groups in scope to the tables of metrics Q1 to Q9,
U8 (the rebooking funnel) and U11 (participants per call). It holds no database and no text:
rows carry codes, the page turns them into words. Every count goes through the small-cell rules
of ``usage_suppress``; a mean or a ratio is shown only when the figures it is made from are.

    cells ─► group-local day ─► counts and buckets per metric ─► ``suppress_*`` ─► ``QueueReport``

The cells are those the call and rebooking facts feed (ADR 0117 point 6); with no such cells, for
instance before those facts are written, every table is empty and says so, never an error.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from app.domain.booking import BookingKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import ActorClass, UsageCell
from app.domain.usage_report import (
    SIGNED_BUCKETS,
    UNSIGNED_BUCKETS,
    CountTable,
    Distribution,
    GridTable,
    ReportGroup,
    count_table,
    distribution,
    grid_table,
    headline_count,
    local_cells,
    zones_of,
)

REBOOK_PROPOSED = "proposed"
REBOOK_ACCEPTED = "accepted"
REBOOK_EXPIRED = "expired"
"""The ``reason_code`` words of the rebooking cells (metric U8)."""
CALL_CREATED = "created"
CALL_CLAIMED = "claimed"
CALL_RELEASED = "released"
"""The ``reason_code`` words of the direct-call and own-queue cells (metrics Q6 and Q7)."""
PLANNER_ASKED = ActorClass.PLANNER.value
ASSISTANT_PROPOSED = ActorClass.SYSTEM.value
"""Who proposed a new time: a planner who asked, or the assistant of ADR 0057."""
METRICS: frozenset[str] = frozenset(
    {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "U8", "U11"}
)
"""The metrics of this screen: a cell of any other metric is none of its business."""
FAILURES = ("task_failed", "meeting_failed", "integration_error")
"""The kinds of dispatch failure that always have a row, even with no failure (metric Q8)."""


@dataclass(frozen=True)
class DayPeak:
    """The most direct calls waiting or connected at once in a group on one local day (Q9)."""

    day: date
    peak: int | None


@dataclass(frozen=True)
class QueueReport:
    """Everything the *Queue and calls* screen shows, with small cells already hidden.

    Attributes:
        first_day: The first day of the range (group-local).
        last_day: The last day of the range.
        as_of: When the rollup last ran.
        threshold: The small-cell threshold that was applied.
        empty: Whether the range holds no cell of this screen's metrics at all (nothing is
            recorded yet, or reporting is off); the page says so instead of a table of zeros.
        mix: Bookings by queue method and kind of booking (Q1).
        task_wait: The wait from a task being made to the call connecting (Q2).
        punctuality: When the host joined against the start, early below zero (Q3).
        guest_wait: How long a participant waited in the meeting for the host (Q4).
        call_length: How long calls lasted (Q5).
        calls_ended: Finished calls (U11), ``None`` when a small cell.
        participants_per_call_tenths: Mean people per finished call in tenths (U11).
        direct_created: Direct calls started (Q6).
        direct_left: Direct calls the participant left while waiting (Q6).
        direct_not_picked_up: Direct calls nobody picked up in time (Q6).
        abandoned_percent: ``direct_left`` over ``direct_created`` in whole percent (Q6).
        abandon_wait: The wait at the moment of leaving (Q6).
        claimed: Bookings claimed in the own queue (Q7).
        released: Claims given back (Q7).
        releases_per_claim_tenths: Releases per claim in tenths (Q7).
        claim_wait: The time from the queue showing a booking to its claim (Q7).
        failures: Dispatch failures per code (Q8).
        peaks: The peak load per local day (Q9).
        peak: The highest peak of the range, ``None`` when no day may be shown.
        rebook_proposed: New times proposed, per who proposed (U8).
        rebook_accepted: Proposals accepted.
        rebook_expired: Proposals that lapsed.
        rebook_conversion_percent: ``accepted`` over ``proposed`` in whole percent.
        rebook_wait: The time to accept.
    """

    first_day: date
    last_day: date
    as_of: datetime | None
    threshold: int
    empty: bool
    mix: GridTable
    task_wait: Distribution
    punctuality: Distribution
    guest_wait: Distribution
    call_length: Distribution
    calls_ended: int | None
    participants_per_call_tenths: int | None
    direct_created: int | None
    direct_left: int | None
    direct_not_picked_up: int | None
    abandoned_percent: int | None
    abandon_wait: Distribution
    claimed: int | None
    released: int | None
    releases_per_claim_tenths: int | None
    claim_wait: Distribution
    failures: CountTable
    peaks: tuple[DayPeak, ...]
    peak: int | None
    rebook_proposed: CountTable
    rebook_accepted: int | None
    rebook_expired: int | None
    rebook_conversion_percent: int | None
    rebook_wait: Distribution


def _ratio_percent(part: int | None, whole: int | None) -> int | None:
    """Return ``part`` over ``whole`` in whole percent, or ``None`` if either is hidden."""
    if part is None or whole is None or whole <= 0:
        return None
    return round(100 * part / whole)


def _tenths(part: int | None, whole: int | None) -> int | None:
    """Return ``part`` over ``whole`` in tenths, or ``None`` if either is hidden."""
    if part is None or whole is None or whole <= 0:
        return None
    return round(10 * part / whole)


def queue_report(
    cells: Sequence[UsageCell],
    groups: Sequence[ReportGroup],
    first_day: date,
    last_day: date,
    *,
    threshold: int,
    as_of: datetime | None,
) -> QueueReport:
    """Build the *Queue and calls* tables from ``cells`` of ``groups`` over the range.

    Only cells of the given groups count, and only those whose hour falls on a day of the range
    in that group's time zone. ``threshold`` is the effective small-cell threshold.
    """
    by_metric: dict[str, list[UsageCell]] = {}
    mix: Counter[tuple[str, str]] = Counter()
    ended = people = 0
    peak_count: Counter[date] = Counter()
    peak_max: dict[date, int] = {}
    failures: Counter[str] = Counter()
    rebook: Counter[str] = Counter()
    created = left = unpicked = claimed = released = 0
    for cell, local in local_cells(cells, zones_of(groups), first_day, last_day):
        if cell.metric not in METRICS:
            continue
        by_metric.setdefault(cell.metric, []).append(cell)
        if cell.metric == "Q1" and cell.dispatch_kind and cell.booking_kind:
            mix[(cell.dispatch_kind.value, cell.booking_kind.value)] += cell.count
        elif cell.metric == "U11":
            ended += cell.count
            people += cell.sum_value
        elif cell.metric == "Q9":
            peak_count[local.date()] += cell.count
            peak_max[local.date()] = max(peak_max.get(local.date(), 0), cell.max_value)
        elif cell.metric == "Q8" and cell.reason_code:
            failures[cell.reason_code] += cell.count
        elif cell.metric == "Q6":
            if cell.bucket is not None:
                left += cell.count
            elif cell.reason_code == CALL_CREATED:
                created += cell.count
            else:
                unpicked += cell.count
        elif cell.metric == "Q7" and cell.reason_code == CALL_CLAIMED:
            claimed += cell.count
        elif cell.metric == "Q7" and cell.reason_code == CALL_RELEASED:
            released += cell.count
        elif cell.metric == "U8" and cell.reason_code:
            key = cell.reason_code
            if key == REBOOK_PROPOSED:
                key = f"{REBOOK_PROPOSED}.{cell.actor_class.value if cell.actor_class else ''}"
            rebook[key] += cell.count
    shown_created, shown_left = headline_count(created, threshold), headline_count(left, threshold)
    shown_claimed, shown_released = (
        headline_count(claimed, threshold),
        headline_count(released, threshold),
    )
    shown_ended = headline_count(ended, threshold)
    accepted = headline_count(rebook[REBOOK_ACCEPTED], threshold)
    proposed = count_table(
        rebook,
        [f"{REBOOK_PROPOSED}.{PLANNER_ASKED}", f"{REBOOK_PROPOSED}.{ASSISTANT_PROPOSED}"],
        threshold,
    )
    peaks = tuple(
        DayPeak(d, peak_max[d] if peak_count[d] >= threshold else None) for d in sorted(peak_max)
    )
    shown_peaks = [p.peak for p in peaks if p.peak is not None]
    codes = [*FAILURES, *sorted(set(failures) - set(FAILURES))]
    return QueueReport(
        first_day=first_day,
        last_day=last_day,
        as_of=as_of,
        threshold=threshold,
        empty=not by_metric,
        mix=grid_table(
            mix,
            [k.value for k in DispatchKind],
            [k.value for k in BookingKind],
            threshold,
        ),
        task_wait=distribution(by_metric.get("Q2", ()), UNSIGNED_BUCKETS, threshold),
        punctuality=distribution(
            by_metric.get("Q3", ()), SIGNED_BUCKETS, threshold, drop_empty=True
        ),
        guest_wait=distribution(by_metric.get("Q4", ()), UNSIGNED_BUCKETS, threshold),
        call_length=distribution(by_metric.get("Q5", ()), UNSIGNED_BUCKETS, threshold),
        calls_ended=shown_ended,
        participants_per_call_tenths=_tenths(people if shown_ended else None, ended),
        direct_created=shown_created,
        direct_left=shown_left,
        direct_not_picked_up=headline_count(unpicked, threshold),
        abandoned_percent=_ratio_percent(shown_left, shown_created),
        abandon_wait=distribution(
            [c for c in by_metric.get("Q6", ()) if c.bucket is not None],
            UNSIGNED_BUCKETS,
            threshold,
        ),
        claimed=shown_claimed,
        released=shown_released,
        releases_per_claim_tenths=_tenths(shown_released, shown_claimed),
        claim_wait=distribution(
            [c for c in by_metric.get("Q7", ()) if c.reason_code == CALL_CLAIMED],
            UNSIGNED_BUCKETS,
            threshold,
        ),
        failures=count_table(failures, codes, threshold),
        peaks=peaks,
        peak=max(shown_peaks) if shown_peaks else None,
        rebook_proposed=proposed,
        rebook_accepted=accepted,
        rebook_expired=headline_count(rebook[REBOOK_EXPIRED], threshold),
        rebook_conversion_percent=_ratio_percent(accepted, proposed.total),
        rebook_wait=distribution(
            [c for c in by_metric.get("U8", ()) if c.reason_code == REBOOK_ACCEPTED],
            UNSIGNED_BUCKETS,
            threshold,
        ),
    )
