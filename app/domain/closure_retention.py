"""How long a booking on hold keeps its personal data: the absolute maximum (ADR 0102).

A booking suspended by an emergency closure is purged at

    purge_moment = min(closure_end + RETENTION_AFTER_CLOSURE,
                       original_slot_end + max_retention)

where ``closure_end`` is absent while the closure is open, so an open closure falls back to the
second term. The 30 days after the end of a closure stay as ADR 0024 decided them; the maximum
(``closure_max_retention_days``, class D, 30 to 180 days, no way to switch it off) only cuts them
short. Every consumer of the moment (the job plan, the retention overview and the nightly sweep)
calls ``suspended_purge_moment`` so they cannot disagree.

``ClosureRetention`` is the organisation's pair of settings; consumers receive a function from an
organisation id to it so a change in the portal applies to bookings not yet purged.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

RETENTION_AFTER_CLOSURE = timedelta(days=30)
"""How long a still suspended booking keeps its personal data after the closure ends."""


@dataclass(frozen=True)
class ClosureRetention:
    """The organisation's limits for bookings on hold (ADR 0102, class D settings).

    Attributes:
        max_retention: ``closure_max_retention_days``: the most personal data of a suspended
            booking is kept after the end of its original slot, whatever the closure does.
        notice: ``closure_max_notice_days``: how long before the maximum the administrators
            are told that a closure still holds data.
    """

    max_retention: timedelta
    notice: timedelta

    @classmethod
    def from_days(cls, max_retention_days: int, notice_days: int) -> "ClosureRetention":
        """Return the limits from the two settings, which are whole days."""
        return cls(timedelta(days=max_retention_days), timedelta(days=notice_days))


DEFAULT_CLOSURE_RETENTION = ClosureRetention.from_days(90, 14)
"""The defaults of ``closure_max_retention_days`` and ``closure_max_notice_days`` (ADR 0102)."""

ClosureRetentionOf = Callable[[int], ClosureRetention]
"""Maps an organisation id to its limits, read live from the settings."""


def default_closure_retention(_organisation_id: int) -> ClosureRetention:
    """Return the default limits for any organisation (tests, tools without portal values)."""
    return DEFAULT_CLOSURE_RETENTION


def maximum_moment(slot_end: datetime, retention: ClosureRetention) -> datetime:
    """Return the absolute maximum: the original slot end plus the maximum retention."""
    return slot_end + retention.max_retention


def suspended_purge_moment(
    slot_end: datetime, closure_ends_at: datetime | None, retention: ClosureRetention
) -> datetime:
    """Return when the personal data of a booking on hold must go (never ``None``).

    Args:
        slot_end: The end of the slot the booking still occupies (its original appointment).
        closure_ends_at: The end of its closure, ``None`` while the closure is open.
        retention: The organisation's limits.
    """
    maximum = maximum_moment(slot_end, retention)
    if closure_ends_at is None:
        return maximum
    return min(closure_ends_at + RETENTION_AFTER_CLOSURE, maximum)


def is_capped(
    slot_end: datetime, closure_ends_at: datetime | None, retention: ClosureRetention
) -> bool:
    """Return whether the maximum, not the end of the closure, decides the purge moment."""
    return (
        closure_ends_at is None
        or maximum_moment(slot_end, retention) < closure_ends_at + RETENTION_AFTER_CLOSURE
    )
