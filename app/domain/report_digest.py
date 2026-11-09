"""The digest mail of the usage reports: subscriptions, when one is due, what it covers (ADR 0117).

A subscription belongs to an organisation or to one of its groups and has a period: daily, weekly
on a chosen weekday or monthly on a chosen day of the month (1 to 28, so every month has it). The
mail goes out at the organisation's send hour in the group's time zone, covering the days since
the last mail and ending yesterday. Pure functions only; the service that sends is
``report_digest_service`` and the store is ``app/adapters/db/report_digest.py``.

    now, zone, send hour ─► ``latest_due`` ─► the local date the digest is for (its run key)
                          └► ``window_of`` ─► the first and last day the figures cover
                          └► ``is_late``   ─► whether the window was missed for good

A run is keyed on ``(subscription, due date)``: the store refuses a second run for the same key,
so a retry or a second worker never sends twice.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

MAX_MONTH_DAY = 28
"""The last day of a month a monthly digest may be set to; every month has it."""


class DigestPeriod(StrEnum):
    """How often a digest goes out."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class RunStatus(StrEnum):
    """What came of one due digest."""

    SENT = "sent"
    SKIPPED_LATE = "skipped_late"
    NO_RECIPIENT = "no_recipient"


@dataclass(frozen=True)
class Subscription:
    """A digest subscription.

    Attributes:
        id: Database id; ``None`` before it is stored.
        organisation_id: The organisation it belongs to.
        organisation_slug: Its slug.
        group_id: The group, or ``None`` for the whole organisation.
        group_slug: Its slug.
        period: Daily, weekly or monthly.
        weekday: Monday is 0; used by a weekly digest.
        month_day: 1 to ``MAX_MONTH_DAY``; used by a monthly digest.
        recipients: Row ids of the staff members it is addressed to; never an address.
        created_by: The subject who made it.
        created_at: When.
    """

    id: int | None
    organisation_id: int
    organisation_slug: str
    group_id: int | None
    group_slug: str | None
    period: DigestPeriod
    weekday: int
    month_day: int
    recipients: tuple[int, ...]
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        """Refuse a weekday or a day of the month that does not exist.

        Raises:
            ValueError: For a weekday outside 0 to 6 or a day outside 1 to ``MAX_MONTH_DAY``.
        """
        if not 0 <= self.weekday <= 6 or not 1 <= self.month_day <= MAX_MONTH_DAY:
            raise ValueError("weekday or day of the month out of range")


def runs_on(subscription: Subscription, day: date) -> bool:
    """Return whether a digest of ``subscription`` goes out on the local ``day``."""
    if subscription.period is DigestPeriod.DAILY:
        return True
    if subscription.period is DigestPeriod.WEEKLY:
        return day.weekday() == subscription.weekday
    return day.day == subscription.month_day


def latest_due(
    subscription: Subscription, now: datetime, zone: ZoneInfo, send_hour: int
) -> date | None:
    """Return the local date of the latest digest that is due at ``now``, or ``None``.

    Today counts from the send hour on; a date before today always has passed. The search goes
    back at most 35 days, the longest gap between two monthly digests plus a margin.
    """
    local = now.astimezone(zone)
    for back in range(36):
        day = local.date() - timedelta(days=back)
        if back == 0 and local.hour < send_hour:
            continue
        if runs_on(subscription, day):
            return day
    return None


def due_at(day: date, zone: ZoneInfo, send_hour: int) -> datetime:
    """Return the moment, in the group's zone, the digest of ``day`` is due."""
    return datetime.combine(day, time(send_hour), zone)


def is_late(day: date, now: datetime, zone: ZoneInfo, send_hour: int, late_hours: int) -> bool:
    """Return whether the digest of ``day`` is later than ``late_hours`` after its due moment."""
    return now - due_at(day, zone, send_hour) > timedelta(hours=late_hours)


def window_of(subscription: Subscription, day: date) -> tuple[date, date]:
    """Return the first and the last day the digest sent on ``day`` covers.

    It ends yesterday and starts a period earlier: a day, seven days, or back to the same day of
    the month before.
    """
    last = day - timedelta(days=1)
    if subscription.period is DigestPeriod.DAILY:
        return last, last
    if subscription.period is DigestPeriod.WEEKLY:
        return day - timedelta(days=7), last
    year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    return date(year, month, day.day), last
