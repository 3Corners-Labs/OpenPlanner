"""The data and rules for merging old booking rows into daily counts (ADR 0102 step 4).

The service that runs them is ``booking_rollup_service``.

After the purge a booking row holds no personal data any more (``privacy.PURGED_FIELDS``),
but its times and reference could still point to a person together with data held elsewhere. The
row therefore goes too, ``booking_row_retention_days`` after ``pii_purged_at``. What statistics
need survives as a count per group, day and final status:

    list rollable ─► row still holds data or an open job? ─► left alone, tried again later
                  ─► count per (group, day, status) ─► add to the day counts
                  ─► delete the rows, their events and call status   (one transaction per batch)
                  ─► one system event ``booking_rows.rollup`` (counts only)

The merge and the delete share a transaction, so a row is never counted twice or lost, and a
second run finds nothing left to do (idempotent). The day is the UTC date of the slot start.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.booking import BookingStatus

BATCH_SIZE = 200
"""Rows merged and deleted per transaction: a fixed bound on the write lock, not a policy."""


@dataclass(frozen=True)
class RollableBooking:
    """What the rollup needs to know about one purged booking row.

    Attributes:
        booking_id: The row.
        group_id: The group it belongs to.
        day: UTC date of the slot start.
        status: The final status of the booking.
        purged_at: ``pii_purged_at``, or ``None`` while the row holds personal data.
        holds_data: Whether any column of ``privacy.PURGED_FIELDS`` is still filled.
        open_jobs: Pending or running jobs of the booking.
    """

    booking_id: int
    group_id: int
    day: date
    status: BookingStatus
    purged_at: datetime | None
    holds_data: bool
    open_jobs: int


@dataclass(frozen=True)
class DayCount:
    """Bookings of one group on one day that ended in one status, with no personal data.

    Attributes:
        group_id: The group.
        day: UTC date of the slot start.
        status: The final status.
        count: How many bookings.
        last_purged_at: The latest ``pii_purged_at`` among them (the retention overview's
            *Last removed* keeps working after the rows are gone).
    """

    group_id: int
    day: date
    status: BookingStatus
    count: int
    last_purged_at: datetime


@dataclass(frozen=True)
class RollupResult:
    """What one run did: counts only.

    Attributes:
        rows: Booking rows merged and deleted.
        day_counts: Distinct (group, day, status) counts the rows went into.
        skipped: Rows past the term that were left alone (personal data or an open job).
    """

    rows: int = 0
    day_counts: int = 0
    skipped: int = 0


def purged_before(now: datetime, retention: timedelta) -> datetime:
    """Return the moment before which ``pii_purged_at`` must lie for a row to be rolled up."""
    return now - retention


def is_rollable(row: RollableBooking, now: datetime, retention: timedelta) -> bool:
    """Return whether the row may be merged and deleted now.

    Never a row that still holds personal data, has no purge moment, or has an open job.
    """
    return (
        row.purged_at is not None
        and row.purged_at <= purged_before(now, retention)
        and not row.holds_data
        and row.open_jobs == 0
    )


def tally(rows: Sequence[RollableBooking]) -> list[DayCount]:
    """Return the rows counted per group, day and status, in a stable order."""
    counts: Counter[tuple[int, date, BookingStatus]] = Counter()
    latest: dict[tuple[int, date, BookingStatus], datetime] = {}
    for row in rows:
        key = (row.group_id, row.day, row.status)
        counts[key] += 1
        if row.purged_at is not None and (key not in latest or row.purged_at > latest[key]):
            latest[key] = row.purged_at
    return [
        DayCount(key[0], key[1], key[2], counts[key], latest[key])
        for key in sorted(counts, key=lambda k: (k[0], k[1], k[2].value))
    ]
